.PHONY: setup start stop

setup:
	@test -f .env || cp -n .env.example .env
	@echo 'Add your Groq API key to .env.'
	@mkdir -p build/SystemCapture.app/Contents/MacOS build/swift-cache
	@cp -f native/Info.plist build/SystemCapture.app/Contents/Info.plist
	@swiftc -module-cache-path build/swift-cache -o build/SystemCapture.app/Contents/MacOS/SystemCapture native/SystemCapture.swift native/main.swift
	@codesign --force --sign - build/SystemCapture.app

start:
	@python3 app.py

stop:
	@python3 -c "import json, os, urllib.request; req = urllib.request.Request('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/shutdown', data=b'{}', headers={'Content-Type': 'application/json'}, method='POST'); urllib.request.urlopen(req, timeout=15).read()"
