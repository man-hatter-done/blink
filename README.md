# Blink Terminal Backend

A Flask-based backend service that provides terminal functionality via WebSockets and HTTP API, designed to work with the Blink iOS client.

## Features

- Terminal session management
- Command execution with real-time output streaming
- WebSocket communication for live terminal interaction
- HTTP API for clients that don't support WebSockets
- Session persistence with automatic renewal

## API Key

This backend uses the API key "B2D4G5" which matches the hardcoded key in the Swift client.

## Deployment

The easiest way to deploy this backend is with Render.com using the included `render.yaml` file.
Once deployed, update the `baseURL` in your Swift client to point to your Render.com URL.
