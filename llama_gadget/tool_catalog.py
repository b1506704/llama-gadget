"""Frozen routing and schemas for local and MCP tools in a chat turn."""
import copy
import hashlib
import json
import re

from .tools import DEFINITIONS, tool_error


def external_refs(value):
    if isinstance(value,dict):
        return any((key in ('$ref','$dynamicRef') and isinstance(item,str) and not item.startswith('#'))
                   or external_refs(item) for key,item in value.items())
    if isinstance(value,list):
        return any(external_refs(item) for item in value)
    return False


class ToolCatalog:
    def __init__(self,local_registry=None,manager=None,snapshot=()):
        self.local = local_registry
        self.workspace = local_registry.workspace if local_registry else None
        self.manager = manager
        self.routes = {}
        self.issues = []
        self._schemas = local_registry.schemas() if local_registry else []
        for entry in copy.deepcopy(snapshot):
            original = entry['server']+'\0'+entry['original_name']
            prefix = re.sub(r'[^A-Za-z0-9_-]','_',f"mcp_{entry['server']}_{entry['original_name']}")[:47]
            name = prefix+'_'+hashlib.sha256(original.encode()).hexdigest()[:16]
            try:
                from jsonschema.validators import validator_for
                schema = entry['input_schema']
                if not isinstance(schema,dict) or schema.get('type') != 'object' or external_refs(schema):
                    raise ValueError('Tool input must be an object schema with only local references')
                validator = validator_for(schema)
                validator.check_schema(schema)
                if name in self.routes:
                    raise ValueError('Duplicate MCP tool name')
                entry['validator'] = validator(schema)
                self.routes[name] = entry
                self._schemas.append(dict(type='function',function=dict(name=name,
                    description=entry.get('description',''),parameters=schema)))
            except Exception:
                self.issues.append(dict(server=entry['server'],name=entry['original_name'],error='Invalid or duplicate tool schema'))

    def schemas(self):
        return copy.deepcopy(self._schemas)

    def describe(self,name):
        if name in self.routes:
            entry=self.routes[name]
            return {'server':entry['server'],'name':entry['original_name'],'approval':'Always requires approval'}
        if self.local and name in DEFINITIONS:
            return {'server':'Built-in','name':name,'approval':'Approval required' if self.local.requires_approval(name) else 'Automatic in workspace'}
        raise ValueError('Tool is unavailable')

    def requires_approval(self,name):
        return True if name in self.routes else self.local.requires_approval(name)

    def prepare(self,name,arguments):
        if name not in self.routes:
            if self.local:
                return self.local.prepare(name,arguments)
            raise ValueError('Unknown or unavailable tool')
        try:
            args = json.loads(arguments) if isinstance(arguments,str) else copy.deepcopy(arguments)
            self.routes[name]['validator'].validate(args)
            return args
        except Exception as exc:
            raise ValueError('MCP arguments do not match the tool input schema') from exc

    def execute(self,name,arguments,cancel_event,*,prepared=False):
        try:
            if name not in self.routes:
                if self.local:
                    return self.local.execute(name,arguments,cancel_event,prepared=prepared)
                raise ValueError('Unknown or unavailable tool')
            args = self.prepare(name,arguments)
            entry = self.routes[name]
            return self.manager.call(entry['server'],entry['original_name'],args,cancel_event,generation=entry['generation'])
        except Exception as exc:
            return tool_error(exc)
