/**
 * GET /.netlify/functions/get-submissions
 * Returns all pending submissions for the station-corrections Netlify Form.
 * Requires X-Admin-Token header matching ADMIN_TOKEN env var.
 */
exports.handler = async function(event) {
  // Only allow GET
  if (event.httpMethod !== 'GET') {
    return { statusCode: 405, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  // Auth
  const adminToken = process.env.ADMIN_TOKEN;
  if (!adminToken || event.headers['x-admin-token'] !== adminToken) {
    return { statusCode: 401, body: JSON.stringify({ error: 'Unauthorized' }) };
  }

  const netlifyToken  = process.env.NETLIFY_ACCESS_TOKEN;
  const formId        = process.env.NETLIFY_FORM_ID;

  if (!netlifyToken || !formId) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'Server misconfiguration: missing NETLIFY_ACCESS_TOKEN or NETLIFY_FORM_ID' }),
    };
  }

  try {
    const url = `https://api.netlify.com/api/v1/forms/${formId}/submissions?per_page=100`;
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${netlifyToken}` },
    });

    if (!resp.ok) {
      const text = await resp.text();
      return {
        statusCode: resp.status,
        body: JSON.stringify({ error: `Netlify API error: ${resp.status}`, detail: text }),
      };
    }

    const data = await resp.json();
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: err.message }) };
  }
};
