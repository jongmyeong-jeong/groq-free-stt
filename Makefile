.PHONY: setup start test

setup:
	@test -f .env || cp -n .env.example .env
	@echo 'Add your Groq API key to .env.'

start:
	@python3 app.py

test:
	@python3 -m unittest discover -s tests -v
