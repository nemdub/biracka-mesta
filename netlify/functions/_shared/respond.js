const JSON_TYPE = 'application/json; charset=utf-8';

// Success responses are cached: 1 min in browsers, 5 min at Netlify's CDN,
// with 10 min stale-while-revalidate so a brief origin hiccup doesn't drop
// users. Cache key is URL-only (no Vary), which means a response cached for
// one valid API key is served to all callers requesting the same URL —
// intentional, the data is identical per query.
const OK_HEADERS = {
  'Content-Type': JSON_TYPE,
  'Cache-Control': 'public, max-age=60',
  'Netlify-CDN-Cache-Control': 'public, s-maxage=300, stale-while-revalidate=600',
};

const ERR_HEADERS = {
  'Content-Type': JSON_TYPE,
  'Cache-Control': 'no-store',
  'Netlify-CDN-Cache-Control': 'no-store',
};

function ok(body) {
  return {
    statusCode: 200,
    headers: OK_HEADERS,
    body: JSON.stringify(body),
  };
}

function err(statusCode, code, message) {
  return {
    statusCode,
    headers: ERR_HEADERS,
    body: JSON.stringify({ error: message, code }),
  };
}

module.exports = { ok, err };
