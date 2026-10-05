import unittest
from PyQt6.QtWidgets import QApplication, QPushButton
from PyQt6.QtGui import QImage, QColor
from llama_gadget.chat_ui import ChatTranscript, AttachmentStrip, ImagePreviewDialog
from llama_gadget.images import encode_image, decode_attachment_image

APP = QApplication.instance() or QApplication([])


class ChatUITests(unittest.TestCase):
    def test_tools_hidden_prose_and_copy(self):
        view = ChatTranscript()
        self.addCleanup(view.deleteLater)
        text = 'Answer\n```python\nprint(1)\n```'
        messages = [dict(id=1, role='assistant', content='Checking', metadata={'tool_calls': [
            {'id':'call_1', 'function': {'name':'read_file', 'arguments':'{"path":"secret"}'}}]}),
            dict(id=2, role='tool', content='{"output":"private result"}', metadata={'name':'read_file'}),
            dict(id=3, role='assistant', content=text)]
        view.set_messages(messages)
        self.assertIn('Checking', view.toPlainText())
        self.assertNotIn('private result', view.toPlainText())
        self.assertNotIn('read_file', view.toPlainText())
        buttons = [b for b in view.findChildren(QPushButton) if b.text() == 'Copy']
        self.assertEqual(len(buttons), 2)
        buttons[-1].click()
        self.assertEqual(APP.clipboard().text(), text)
        self.assertEqual(buttons[-1].text(), 'Copied')
        view.set_messages(messages, show_tools=True)
        self.assertIn('private result', view.toPlainText())
        self.assertIn('secret', view.toPlainText())
        view.set_messages([], live_reply='live')
        self.assertNotIn('private result', view.toPlainText())

    def test_stored_image_and_stale_composer_button(self):
        image = QImage(32, 16, QImage.Format.Format_RGB32)
        image.fill(QColor('red'))
        attachment = encode_image(image, 'pasted.jpg')
        self.assertEqual(decode_attachment_image(attachment).size(), image.size())
        strip = AttachmentStrip()
        self.addCleanup(strip.deleteLater)
        seen = []
        strip.image_requested.connect(seen.append)
        strip.set_attachments([attachment])
        button = strip.findChild(QPushButton)
        button.click()
        self.assertEqual(seen[-1]['data'], attachment['data'])
        preview = ImagePreviewDialog(seen[-1])
        self.addCleanup(preview.deleteLater)
        self.assertEqual(preview.image.size(), image.size())
        preview.actual_size_cb.setChecked(True)
        strip.set_attachments([])
        self.assertEqual(strip.findChildren(QPushButton), [])

    def test_corrupt_image(self):
        with self.assertRaises(ValueError):
            decode_attachment_image({'data':'data:image/jpeg;base64,broken'})

    def test_conversation_image(self):
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor('blue'))
        attachment = encode_image(image, 'old.jpg')
        view = ChatTranscript()
        self.addCleanup(view.deleteLater)
        seen = []
        view.image_requested.connect(seen.append)
        view.set_messages([dict(role='user', content='Look', attachments=[attachment])])
        button = next(b for b in view.findChildren(QPushButton) if 'old.jpg' in b.text())
        button.click()
        self.assertEqual(seen[0]['data'], attachment['data'])
