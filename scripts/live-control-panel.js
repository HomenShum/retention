/**
 * Live Control Panel — serves a web page with:
 *   1. Embedded noVNC viewer (live screen)
 *   2. Voice control via OpenAI Realtime API
 *   3. Quick action buttons
 *   4. Command input
 *
 * Runs on CONTROL_PANEL_PORT (default 6090)
 */

const http = require("http");
const fs = require("fs");
const path = require("path");

const crypto = require("crypto");
const PORT = parseInt(process.env.CONTROL_PANEL_PORT || "6090");
const WEBSOCKIFY_PORT = parseInt(process.env.WEBSOCKIFY_PORT || "6080");
const OPENAI_API_KEY = process.env.OPENAI_API_KEY || "";
const AUTH_PASSWORD = process.env.LIVE_CONTROL_PASSWORD || "";

// --- Security: session-based auth with rate limiting ---
const SESSION_SECRET = crypto.randomBytes(32).toString("hex"); // rotates every restart
const activeSessions = new Map(); // sessionId -> { created, ip }
const SESSION_TTL_MS = 4 * 60 * 60 * 1000; // 4 hours

// Rate limiting: max 5 failed login attempts per IP per 10 minutes
const loginAttempts = new Map(); // ip -> { count, firstAttempt }
const MAX_ATTEMPTS = 5;
const ATTEMPT_WINDOW_MS = 10 * 60 * 1000;

function createSession(ip) {
  const sessionId = crypto.randomBytes(32).toString("hex");
  activeSessions.set(sessionId, { created: Date.now(), ip });
  return sessionId;
}

function isValidSession(sessionId, ip) {
  const session = activeSessions.get(sessionId);
  if (!session) return false;
  if (Date.now() - session.created > SESSION_TTL_MS) {
    activeSessions.delete(sessionId);
    return false;
  }
  // Session is bound to the IP that created it
  if (session.ip !== ip) return false;
  return true;
}

function getClientIp(req) {
  return req.headers["cf-connecting-ip"] || req.headers["x-forwarded-for"]?.split(",")[0]?.trim() || req.socket.remoteAddress || "unknown";
}

function isRateLimited(ip) {
  const record = loginAttempts.get(ip);
  if (!record) return false;
  if (Date.now() - record.firstAttempt > ATTEMPT_WINDOW_MS) {
    loginAttempts.delete(ip);
    return false;
  }
  return record.count >= MAX_ATTEMPTS;
}

function recordFailedLogin(ip) {
  const record = loginAttempts.get(ip);
  if (!record || Date.now() - record.firstAttempt > ATTEMPT_WINDOW_MS) {
    loginAttempts.set(ip, { count: 1, firstAttempt: Date.now() });
  } else {
    record.count++;
  }
}

function getSessionFromCookie(req) {
  const cookies = req.headers.cookie || "";
  const match = cookies.match(/(?:^|;\s*)oc_session=([a-f0-9]+)/);
  return match ? match[1] : null;
}

function checkAuth(req, res) {
  if (!AUTH_PASSWORD) {
    // NO PASSWORD SET = REFUSE TO SERVE (safe default)
    res.writeHead(503, { "Content-Type": "text/plain" });
    res.end("Live Control disabled — set LIVE_CONTROL_PASSWORD in .env first");
    return false;
  }

  const ip = getClientIp(req);
  const sessionId = getSessionFromCookie(req);

  if (sessionId && isValidSession(sessionId, ip)) {
    return true; // Authenticated via session cookie
  }

  // Not authenticated — show login page (except for /login POST)
  const url = new URL(req.url, `http://localhost:${PORT}`);
  if (url.pathname === "/login") return true; // Let login handler run
  if (url.pathname === "/api/ping") return true; // Health check

  // Serve login page
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(LOGIN_HTML);
  return false;
}

const LOGIN_HTML = `<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw — Login</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background:#0a0a0a; color:#e0e0e0; height:100vh;
    display:flex; align-items:center; justify-content:center;
    font-family: -apple-system, system-ui, sans-serif;
  }
  .login-box {
    background:#111; border:1px solid #222; border-radius:12px;
    padding:32px; width:320px; text-align:center;
  }
  .login-box h1 { font-size:18px; margin-bottom:8px; }
  .login-box p { font-size:12px; color:#666; margin-bottom:20px; }
  .login-box input {
    width:100%; padding:10px 14px; border-radius:8px;
    border:1px solid #333; background:#1a1a1a; color:#fff;
    font-size:14px; outline:none; margin-bottom:12px;
  }
  .login-box input:focus { border-color:#555; }
  .login-box button {
    width:100%; padding:10px; border-radius:8px; border:none;
    background:#3b82f6; color:#fff; font-size:14px; cursor:pointer;
  }
  .login-box button:hover { background:#2563eb; }
  .error { color:#ef4444; font-size:12px; margin-bottom:12px; display:none; }
</style>
</head><body>
<div class="login-box">
  <h1>OpenClaw Live Control</h1>
  <p>Enter password to access remote desktop</p>
  <div class="error" id="error">Invalid password</div>
  <form onsubmit="return doLogin(event)">
    <input type="password" id="pw" placeholder="Password" autofocus autocomplete="current-password">
    <button type="submit">Connect</button>
  </form>
</div>
<script>
async function doLogin(e) {
  e.preventDefault();
  const pw = document.getElementById('pw').value;
  const resp = await fetch('/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: pw })
  });
  const data = await resp.json();
  if (data.ok) {
    location.reload();
  } else {
    document.getElementById('error').style.display = 'block';
    document.getElementById('error').textContent = data.error || 'Invalid password';
    document.getElementById('pw').value = '';
  }
}
</script>
</body></html>`;

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>OpenClaw Live Control</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0a; color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', system-ui, sans-serif;
    overflow: hidden; height: 100vh; display: flex; flex-direction: column;
  }

  /* Top bar */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 16px; background: #111; border-bottom: 1px solid #222;
    flex-shrink: 0;
  }
  .topbar h1 { font-size: 14px; font-weight: 600; }
  .topbar .status { font-size: 12px; color: #666; }
  .topbar .status.connected { color: #4ade80; }

  /* Main area: screen viewer */
  .viewer {
    flex: 1; position: relative; overflow: hidden;
    display: flex; align-items: center; justify-content: center;
    background: #000;
  }
  .viewer iframe {
    width: 100%; height: 100%; border: none;
  }
  .viewer .placeholder {
    text-align: center; color: #444; font-size: 18px;
  }

  /* Bottom control bar */
  .controls {
    padding: 8px 12px; background: #111; border-top: 1px solid #222;
    display: flex; gap: 8px; align-items: center; flex-shrink: 0;
    flex-wrap: wrap;
  }

  .controls input[type="text"] {
    flex: 1; min-width: 200px; padding: 8px 12px; border-radius: 8px;
    border: 1px solid #333; background: #1a1a1a; color: #fff;
    font-size: 14px; outline: none;
  }
  .controls input[type="text"]:focus { border-color: #555; }

  .controls button {
    padding: 8px 14px; border-radius: 8px; border: 1px solid #333;
    background: #1a1a1a; color: #ccc; font-size: 13px; cursor: pointer;
    white-space: nowrap; transition: all 0.15s;
  }
  .controls button:hover { background: #252525; color: #fff; }
  .controls button:active { background: #333; }
  .controls button.voice { border-color: #dc2626; color: #dc2626; }
  .controls button.voice.active {
    background: #dc2626; color: #fff; animation: pulse 1.5s infinite;
  }
  .controls button.primary { border-color: #3b82f6; color: #3b82f6; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
  }

  /* Quick actions */
  .quick-actions {
    display: flex; gap: 6px; padding: 6px 12px;
    background: #0d0d0d; border-top: 1px solid #1a1a1a;
    overflow-x: auto; flex-shrink: 0;
  }
  .quick-actions button {
    padding: 6px 10px; border-radius: 6px; border: 1px solid #222;
    background: #111; color: #888; font-size: 11px; cursor: pointer;
    white-space: nowrap;
  }
  .quick-actions button:hover { color: #fff; border-color: #444; }

  /* Status overlay */
  .overlay {
    position: absolute; bottom: 12px; right: 12px;
    background: rgba(0,0,0,0.7); backdrop-filter: blur(8px);
    padding: 6px 10px; border-radius: 6px; font-size: 11px; color: #888;
    pointer-events: none;
  }

  /* Voice transcript */
  .transcript {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
    background: rgba(0,0,0,0.8); backdrop-filter: blur(8px);
    padding: 8px 16px; border-radius: 8px; font-size: 13px; color: #fff;
    max-width: 80%; text-align: center; display: none;
  }
  .transcript.visible { display: block; }
</style>
</head>
<body>
  <div class="topbar">
    <h1>OpenClaw Live Control</h1>
    <span class="status" id="status">Connecting...</span>
  </div>

  <div class="viewer" id="viewer">
    <div class="placeholder" id="placeholder">
      <p>Connecting to screen...</p>
      <p style="font-size:13px; margin-top:8px; color:#333">
        VNC via websockify on port ${WEBSOCKIFY_PORT}
      </p>
    </div>
    <div class="transcript" id="transcript"></div>
    <div class="overlay" id="overlay">Latency: --ms</div>
  </div>

  <div class="quick-actions">
    <button onclick="sendCmd('screenshot')">Screenshot</button>
    <button onclick="sendCmd('status')">Status</button>
    <button onclick="sendCmd('open Safari')">Safari</button>
    <button onclick="sendCmd('open Terminal')">Terminal</button>
    <button onclick="sendCmd('open \\'Visual Studio Code\\'')">VS Code</button>
    <button onclick="sendCmd('key cmd+tab')">Cmd+Tab</button>
    <button onclick="sendCmd('key cmd+space')">Spotlight</button>
    <button onclick="sendCmd('shell ls -la')">ls -la</button>
    <button onclick="sendCmd('shell git status')">git status</button>
  </div>

  <div class="controls">
    <button class="voice" id="voiceBtn" onclick="toggleVoice()">Mic</button>
    <input type="text" id="cmdInput" placeholder="Type command (e.g. open Safari, click 100 200, shell ls)..."
           onkeydown="if(event.key==='Enter')sendInput()">
    <button class="primary" onclick="sendInput()">Send</button>
  </div>

<script>
  const WS_PORT = ${WEBSOCKIFY_PORT};
  const statusEl = document.getElementById('status');
  const viewerEl = document.getElementById('viewer');
  const placeholderEl = document.getElementById('placeholder');
  const transcriptEl = document.getElementById('transcript');
  const overlayEl = document.getElementById('overlay');
  const cmdInput = document.getElementById('cmdInput');
  const voiceBtn = document.getElementById('voiceBtn');

  // --- noVNC embed ---
  // We embed the noVNC client directly via iframe
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  // When accessed via tunnel, websockify runs on same origin proxied
  // For local: use localhost:WEBSOCKIFY_PORT
  const isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  let vncUrl;
  if (isLocal) {
    vncUrl = 'http://localhost:' + WS_PORT + '/vnc.html?autoconnect=true&resize=scale&view_only=false';
  } else {
    // Via tunnel — noVNC served from websockify on same tunnel (we proxy it)
    vncUrl = '/vnc-proxy/vnc.html?autoconnect=true&resize=scale&view_only=false&host=' +
             location.hostname + '&port=' + (location.protocol === 'https:' ? 443 : 80) +
             '&path=vnc-proxy/websockify&encrypt=' + (location.protocol === 'https:' ? 'true' : 'false');
  }

  function connectViewer() {
    const iframe = document.createElement('iframe');
    iframe.src = vncUrl;
    iframe.onload = () => {
      placeholderEl.style.display = 'none';
      statusEl.textContent = 'Connected';
      statusEl.classList.add('connected');
    };
    iframe.onerror = () => {
      statusEl.textContent = 'Connection failed';
    };
    viewerEl.insertBefore(iframe, viewerEl.firstChild);
  }

  // --- Command sending (posts to Slack via API relay) ---
  async function sendCmd(cmd) {
    const fullCmd = '!remote ' + cmd;
    statusEl.textContent = 'Sending: ' + cmd;

    try {
      const resp = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: fullCmd })
      });
      const data = await resp.json();
      statusEl.textContent = data.ok ? 'Sent' : 'Failed: ' + (data.error || 'unknown');
      statusEl.classList.toggle('connected', data.ok);
    } catch (e) {
      statusEl.textContent = 'Error: ' + e.message;
    }

    setTimeout(() => {
      statusEl.textContent = 'Connected';
      statusEl.classList.add('connected');
    }, 2000);
  }

  function sendInput() {
    const cmd = cmdInput.value.trim();
    if (cmd) {
      sendCmd(cmd);
      cmdInput.value = '';
    }
  }

  // --- Voice control via OpenAI Realtime API ---
  let voiceActive = false;
  let mediaRecorder = null;
  let audioContext = null;

  async function toggleVoice() {
    if (voiceActive) {
      stopVoice();
    } else {
      startVoice();
    }
  }

  async function startVoice() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceActive = true;
      voiceBtn.classList.add('active');
      voiceBtn.textContent = 'Listening...';
      transcriptEl.classList.add('visible');
      transcriptEl.textContent = 'Listening...';

      // Use WebSocket to our voice relay endpoint
      const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(wsProto + '://' + location.host + '/api/voice');

      ws.onopen = () => {
        // Start sending audio chunks
        audioContext = new AudioContext({ sampleRate: 24000 });
        const source = audioContext.createMediaStreamSource(stream);
        const processor = audioContext.createScriptProcessor(4096, 1, 1);

        processor.onaudioprocess = (e) => {
          if (!voiceActive || ws.readyState !== WebSocket.OPEN) return;
          const float32 = e.inputBuffer.getChannelData(0);
          // Convert to Int16
          const int16 = new Int16Array(float32.length);
          for (let i = 0; i < float32.length; i++) {
            int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768));
          }
          ws.send(int16.buffer);
        };

        source.connect(processor);
        processor.connect(audioContext.destination);
      };

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === 'transcript') {
            transcriptEl.textContent = msg.text;
          } else if (msg.type === 'command') {
            transcriptEl.textContent = 'Executing: ' + msg.command;
            sendCmd(msg.command);
          } else if (msg.type === 'response') {
            transcriptEl.textContent = msg.text;
            // Play audio response if available
            if (msg.audio) {
              playAudioResponse(msg.audio);
            }
          }
        } catch (err) {
          console.error('Voice message parse error:', err);
        }
      };

      ws.onclose = () => {
        stopVoice();
      };

      window._voiceWs = ws;
      window._voiceStream = stream;
    } catch (e) {
      console.error('Voice start failed:', e);
      transcriptEl.textContent = 'Mic access denied';
      transcriptEl.classList.add('visible');
      setTimeout(() => transcriptEl.classList.remove('visible'), 2000);
    }
  }

  function stopVoice() {
    voiceActive = false;
    voiceBtn.classList.remove('active');
    voiceBtn.textContent = 'Mic';
    transcriptEl.classList.remove('visible');

    if (window._voiceWs) {
      window._voiceWs.close();
      window._voiceWs = null;
    }
    if (window._voiceStream) {
      window._voiceStream.getTracks().forEach(t => t.stop());
      window._voiceStream = null;
    }
    if (audioContext) {
      audioContext.close();
      audioContext = null;
    }
  }

  async function playAudioResponse(base64Audio) {
    try {
      const binary = atob(base64Audio);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const ctx = new AudioContext({ sampleRate: 24000 });
      const buffer = await ctx.decodeAudioData(bytes.buffer);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start();
    } catch (e) {
      console.error('Audio playback failed:', e);
    }
  }

  // --- Latency ping ---
  setInterval(async () => {
    const start = Date.now();
    try {
      await fetch('/api/ping');
      overlayEl.textContent = 'Latency: ' + (Date.now() - start) + 'ms';
    } catch (e) {
      overlayEl.textContent = 'Latency: --ms';
    }
  }, 5000);

  // --- Init ---
  connectViewer();
</script>
</body>
</html>`;

// HTTP + WebSocket server
const server = http.createServer((req, res) => {
  if (!checkAuth(req, res)) return;

  const url = new URL(req.url, `http://localhost:${PORT}`);

  // Login endpoint
  if (url.pathname === "/login" && req.method === "POST") {
    const ip = getClientIp(req);

    if (isRateLimited(ip)) {
      res.writeHead(429, { "Content-Type": "application/json" });
      res.end('{"ok":false,"error":"Too many attempts. Try again in 10 minutes."}');
      return;
    }

    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const { password } = JSON.parse(body);
        if (password === AUTH_PASSWORD) {
          const sessionId = createSession(ip);
          res.writeHead(200, {
            "Content-Type": "application/json",
            "Set-Cookie": `oc_session=${sessionId}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=14400`,
          });
          res.end('{"ok":true}');
          console.log(`[auth] Login success from ${ip}`);
        } else {
          recordFailedLogin(ip);
          const remaining = MAX_ATTEMPTS - (loginAttempts.get(ip)?.count || 0);
          res.writeHead(401, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ ok: false, error: `Invalid password (${remaining} attempts left)` }));
          console.log(`[auth] Login FAILED from ${ip} (${remaining} left)`);
        }
      } catch {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end('{"ok":false,"error":"Bad request"}');
      }
    });
    return;
  }

  // Logout
  if (url.pathname === "/logout") {
    const sessionId = getSessionFromCookie(req);
    if (sessionId) activeSessions.delete(sessionId);
    res.writeHead(302, {
      "Set-Cookie": "oc_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
      Location: "/",
    });
    res.end();
    return;
  }

  // Serve main page
  if (url.pathname === "/" || url.pathname === "/index.html") {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(HTML);
    return;
  }

  // Ping
  if (url.pathname === "/api/ping") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"ok":true}');
    return;
  }

  // Command relay — posts to Slack
  if (url.pathname === "/api/command" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const { command } = JSON.parse(body);
        const slackToken = process.env.SLACK_BOT_TOKEN;
        const channel = process.env.CLAW_CHANNEL || "C0AM2J4G6S0";
        const threadFile = "/tmp/openclaw_remote_thread_ts";

        let threadTs = "";
        try {
          threadTs = fs.readFileSync(threadFile, "utf-8").trim();
        } catch {}

        const payload = JSON.stringify({
          channel,
          thread_ts: threadTs || undefined,
          text: command,
          unfurl_links: false,
        });

        const slackReq = http.request(
          {
            hostname: "slack.com",
            path: "/api/chat.postMessage",
            method: "POST",
            headers: {
              Authorization: `Bearer ${slackToken}`,
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
            },
          },
          (slackRes) => {
            let data = "";
            slackRes.on("data", (c) => (data += c));
            slackRes.on("end", () => {
              try {
                const result = JSON.parse(data);
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ ok: result.ok }));
              } catch {
                res.writeHead(500, { "Content-Type": "application/json" });
                res.end('{"ok":false,"error":"parse error"}');
              }
            });
          }
        );
        // Use https
        const https = require("https");
        const sReq = https.request(
          {
            hostname: "slack.com",
            path: "/api/chat.postMessage",
            method: "POST",
            headers: {
              Authorization: `Bearer ${slackToken}`,
              "Content-Type": "application/json",
              "Content-Length": Buffer.byteLength(payload),
            },
          },
          (sRes) => {
            let data = "";
            sRes.on("data", (c) => (data += c));
            sRes.on("end", () => {
              try {
                const result = JSON.parse(data);
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ ok: result.ok }));
              } catch {
                res.writeHead(500);
                res.end('{"ok":false}');
              }
            });
          }
        );
        sReq.write(payload);
        sReq.end();
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }

  // Proxy noVNC files from websockify
  if (url.pathname.startsWith("/vnc-proxy/")) {
    const proxyPath = url.pathname.replace("/vnc-proxy", "");
    const proxyReq = http.request(
      {
        hostname: "localhost",
        port: WEBSOCKIFY_PORT,
        path: proxyPath + url.search,
        method: req.method,
        headers: req.headers,
      },
      (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(res);
      }
    );
    proxyReq.on("error", () => {
      res.writeHead(502);
      res.end("VNC proxy error");
    });
    req.pipe(proxyReq);
    return;
  }

  // 404
  res.writeHead(404);
  res.end("Not found");
});

// Handle WebSocket upgrades for voice and VNC proxy
server.on("upgrade", (req, socket, head) => {
  // Auth check for WebSocket upgrades
  if (AUTH_PASSWORD) {
    const ip = getClientIp(req);
    const sessionId = getSessionFromCookie(req);
    if (!sessionId || !isValidSession(sessionId, ip)) {
      socket.write("HTTP/1.1 401 Unauthorized\r\n\r\n");
      socket.destroy();
      console.log(`[auth] WebSocket upgrade REJECTED from ${ip}`);
      return;
    }
  }

  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === "/api/voice") {
    // Voice WebSocket — relay to OpenAI Realtime API
    handleVoiceUpgrade(req, socket, head);
    return;
  }

  if (url.pathname.startsWith("/vnc-proxy/websockify")) {
    // Proxy WebSocket to websockify
    const net = require("net");
    const proxySocket = net.connect(WEBSOCKIFY_PORT, "localhost", () => {
      // Forward the upgrade request
      const upgradeReq =
        `${req.method} ${req.url.replace("/vnc-proxy", "")} HTTP/${req.httpVersion}\r\n` +
        Object.entries(req.headers)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\r\n") +
        "\r\n\r\n";
      proxySocket.write(upgradeReq);
      if (head.length) proxySocket.write(head);
      proxySocket.pipe(socket);
      socket.pipe(proxySocket);
    });
    proxySocket.on("error", () => socket.destroy());
    socket.on("error", () => proxySocket.destroy());
    return;
  }

  socket.destroy();
});

function handleVoiceUpgrade(req, socket, head) {
  // Minimal WebSocket handshake
  const crypto = require("crypto");
  const key = req.headers["sec-websocket-key"];
  const accept = crypto
    .createHash("sha1")
    .update(key + "258EAFA5-E914-47DA-95CA-5AB5DC30BE12")
    .digest("base64");

  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${accept}\r\n` +
      "\r\n"
  );

  // For now, use Web Speech API on the client side (browser-native)
  // and relay text commands. Full OpenAI Realtime API integration
  // requires connecting to wss://api.openai.com/v1/realtime
  // We'll send back a message telling the client to use browser STT

  const sendWsMessage = (data) => {
    const payload = Buffer.from(JSON.stringify(data));
    const frame = Buffer.alloc(2 + (payload.length > 125 ? 2 : 0) + payload.length);
    frame[0] = 0x81; // text frame, FIN
    if (payload.length <= 125) {
      frame[1] = payload.length;
      payload.copy(frame, 2);
    } else {
      frame[1] = 126;
      frame.writeUInt16BE(payload.length, 2);
      payload.copy(frame, 4);
    }
    socket.write(frame);
  };

  if (!OPENAI_API_KEY) {
    sendWsMessage({
      type: "error",
      text: "No OPENAI_API_KEY configured — voice disabled",
    });
    return;
  }

  // Connect to OpenAI Realtime API
  const WebSocket = require("ws");
  // Note: ws module may not be installed — fall back gracefully
  try {
    const openaiWs = new WebSocket(
      "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview",
      {
        headers: {
          Authorization: `Bearer ${OPENAI_API_KEY}`,
          "OpenAI-Beta": "realtime=v1",
        },
      }
    );

    openaiWs.on("open", () => {
      // Configure session
      openaiWs.send(
        JSON.stringify({
          type: "session.update",
          session: {
            modalities: ["text", "audio"],
            instructions:
              "You are a remote desktop control assistant. The user speaks voice commands to control their Mac. " +
              "Convert their speech into one of these commands and respond with the command format:\n" +
              "- screenshot / ss\n" +
              "- click X Y\n" +
              "- type \"text\"\n" +
              "- key combo (e.g. cmd+c)\n" +
              "- open AppName\n" +
              "- shell command\n" +
              "- status\n" +
              'Respond with JSON: {"command": "the command to execute"}\n' +
              "Keep responses very short. Confirm what you're doing.",
            input_audio_format: "pcm16",
            output_audio_format: "pcm16",
            input_audio_transcription: { model: "whisper-1" },
            turn_detection: {
              type: "server_vad",
              threshold: 0.5,
              silence_duration_ms: 800,
            },
          },
        })
      );

      sendWsMessage({ type: "status", text: "Voice connected" });
    });

    openaiWs.on("message", (data) => {
      try {
        const event = JSON.parse(data);

        if (event.type === "conversation.item.input_audio_transcription.completed") {
          sendWsMessage({ type: "transcript", text: event.transcript });
        }

        if (event.type === "response.text.done") {
          // Try to parse as command
          try {
            const parsed = JSON.parse(event.text);
            if (parsed.command) {
              sendWsMessage({ type: "command", command: parsed.command });
            }
          } catch {
            sendWsMessage({ type: "response", text: event.text });
          }
        }

        if (event.type === "response.audio.delta") {
          sendWsMessage({ type: "audio", data: event.delta });
        }
      } catch {}
    });

    openaiWs.on("error", (err) => {
      sendWsMessage({ type: "error", text: "OpenAI connection error: " + err.message });
    });

    openaiWs.on("close", () => {
      sendWsMessage({ type: "status", text: "Voice disconnected" });
    });

    // Relay audio from client to OpenAI
    socket.on("data", (data) => {
      // Parse WebSocket frame (simplified — assumes no masking issues)
      // In production, use the ws library properly
      if (openaiWs.readyState === WebSocket.OPEN) {
        // Client sends raw PCM16 audio buffers
        const audioBase64 = data.toString("base64");
        openaiWs.send(
          JSON.stringify({
            type: "input_audio_buffer.append",
            audio: audioBase64,
          })
        );
      }
    });

    socket.on("close", () => {
      openaiWs.close();
    });
  } catch (e) {
    sendWsMessage({
      type: "error",
      text: "Voice requires 'ws' npm package. Run: npm install ws",
    });
  }
}

server.listen(PORT, () => {
  console.log(`Live Control Panel running on http://localhost:${PORT}`);
  console.log(`noVNC proxy: /vnc-proxy/ → localhost:${WEBSOCKIFY_PORT}`);
  console.log(`Voice: ${OPENAI_API_KEY ? "enabled" : "disabled (no OPENAI_API_KEY)"}`);
});
