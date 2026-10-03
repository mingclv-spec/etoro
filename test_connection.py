from etoro.config import load_settings
from etoro.client import EtoroClient
s=load_settings(); c=EtoroClient(s)
print(c.get('/watchlists'))
