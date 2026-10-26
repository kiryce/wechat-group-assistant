"""Print non-secret listener and queue diagnostics only."""
from html.parser import HTMLParser
import requests

class TokenParser(HTMLParser):
    token = ''
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'local-token':
            self.token = attrs['content']

if __name__ == '__main__':
    p = TokenParser()
    p.feed(requests.get('http://127.0.0.1:8765/',timeout=5).text)
    state = requests.get('http://127.0.0.1:8765/api/state',headers={'X-Local-Token':p.token},timeout=10).json()
    print(state.get('listener'))
    print('transport:',state['config']['transport'])
    print('groups:',[(g['name'],g['synced_at'],g['sync_error']) for g in state['groups']])
