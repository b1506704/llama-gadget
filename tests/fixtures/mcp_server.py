import asyncio
import os
from pathlib import Path
import sys
from mcp.server import MCPServer
from pydantic import BaseModel

class SumResult(BaseModel):
    result: int

server = MCPServer('fixture')
log_path = os.environ.get('FIXTURE_LOG')
if log_path:
    Path(log_path+'.pid').write_text(str(os.getpid()))

@server.tool()
def add(a: int, b: int) -> SumResult:
    if log_path:
        with open(log_path, 'a') as log:
            log.write('add\n')
    return SumResult(result=a+b)

@server.tool()
async def slow(seconds: float = 5.0) -> str:
    await asyncio.sleep(seconds)
    return 'finished'

@server.tool()
def large() -> str:
    return 'x'*100000

@server.tool()
def secret_error() -> str:
    raise RuntimeError('secret '+os.environ.get('FIXTURE_SECRET',''))

if __name__ == '__main__':
    if len(sys.argv)>1:
        import uvicorn
        uvicorn.run(server.streamable_http_app(), host='127.0.0.1', port=int(sys.argv[1]), log_level='error')
    else:
        server.run()
