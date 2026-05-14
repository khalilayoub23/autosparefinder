# AUTOSPAREFINDER Landing Page

A Vite + React + Tailwind implementation of the AUTOSPAREFINDER landing design.

## Run locally

```bash
npm install
npm run dev
```

Local URL:

- http://localhost:5173

## Production build

```bash
npm run build
npm run preview
```

Preview URL:

- http://localhost:3000

## Docker

Build image:

```bash
docker build -t autosparefinder:latest .
```

Run container:

```bash
docker run --rm -p 3000:3000 autosparefinder:latest
```

## Docker Compose

```bash
docker compose up -d
```

Service:

- autosparefinder
- http://localhost:3000

Health endpoint:

- http://localhost:3000/health
