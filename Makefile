.PHONY: setup start stop test

setup:
	@test -f .env || cp -n .env.example .env
	@echo 'Add your Groq API key to .env.'

start:
	@python3 app.py

stop:
	@python3 -c "import json, os, urllib.request; req = urllib.request.Request('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/shutdown', data=b'{}', headers={'Content-Type': 'application/json'}, method='POST'); urllib.request.urlopen(req, timeout=15).read()"

test:
	@python3 -m unittest discover -s tests -v
