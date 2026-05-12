const crypto = require('crypto');

let cachedKeys = null;
let cachedRaw = null;

function parseKeys() {
  const raw = process.env.API_KEYS ?? '';
  if (raw === cachedRaw && cachedKeys) return cachedKeys;

  const map = new Map();
  for (const entry of raw.split(',')) {
    const trimmed = entry.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf(':');
    if (idx <= 0 || idx === trimmed.length - 1) continue;
    const clientId = trimmed.slice(0, idx).trim();
    const key = trimmed.slice(idx + 1).trim();
    if (clientId && key) map.set(key, clientId);
  }

  cachedRaw = raw;
  cachedKeys = map;
  return map;
}

function constantTimeEquals(a, b) {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

// Returns clientId on success, null on failure.
function authenticate(event) {
  const headers = event.headers || {};
  const provided =
    headers['x-api-key'] ?? headers['X-Api-Key'] ?? headers['X-API-Key'];
  if (!provided || typeof provided !== 'string') return null;

  const keys = parseKeys();
  if (keys.size === 0) return null;

  for (const [key, clientId] of keys) {
    if (constantTimeEquals(provided, key)) return clientId;
  }
  return null;
}

module.exports = { authenticate };
