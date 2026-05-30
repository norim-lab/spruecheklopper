import sys
sys.path.insert(0, '.')
import requests
from cf_solver import load_cached_cookies, get_cf_cookies, get_ua
from sn_complete_scrape import fetch_word, HEADERS

load_cached_cookies()
c = get_cf_cookies()
print(f'Cookies: {len(c) if c else 0}')

session = requests.Session()
session.headers.update(HEADERS)

for word in ['frau', 'singen', 'haus']:
    w, o, m = fetch_word(session, word, cf_cookies=c, cf_ua=get_ua())
    cnt = o.get('count', 'N/A') if o else 'N/A'
    print(f'{word}: outcome={w}, count={cnt}, msg={m}')
