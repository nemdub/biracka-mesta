const JSON_HEADERS = { 'Content-Type': 'application/json; charset=utf-8' };

function ok(body) {
  return {
    statusCode: 200,
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  };
}

function err(statusCode, code, message) {
  return {
    statusCode,
    headers: JSON_HEADERS,
    body: JSON.stringify({ error: message, code }),
  };
}

module.exports = { ok, err };
