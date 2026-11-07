.PHONY: setup start stop test

setup:
	@test -f .env || cp -n .env.example .env
	@echo 'Add your Groq API key to .env.'
	@mkdir -p build/SystemCapture.app/Contents/MacOS build/swift-cache
	@cp -f native/Info.plist build/SystemCapture.app/Contents/Info.plist
	@swiftc -module-cache-path build/swift-cache -o build/SystemCapture.app/Contents/MacOS/SystemCapture native/SystemCapture.swift
	@codesign --force --sign - build/SystemCapture.app

start:
	@python3 app.py

stop:
	@curl --fail --silent --show-error -X POST -H 'Content-Type: application/json' --data '{}' http://127.0.0.1:$${PORT:-8765}/shutdown

test:
	@python3 -m unittest discover -s tests -v
