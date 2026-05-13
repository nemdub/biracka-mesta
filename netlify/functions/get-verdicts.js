/**
 * GET /.netlify/functions/get-verdicts
 * Header: x-admin-token
 *
 * Returns the current data/coord_verdicts.json from GitHub. If the file does
 * not exist yet, returns an empty seed payload (without creating the file).
 */

const { readJsonFile } = require('./_shared/github_file');

const FILE_PATH = 'data/coord_verdicts.json';

function emptySeed() {
  return {
    schema_version: 1,
    updated_at: null,
    decided_by_count: { xlsx: 0, json: 0, neither: 0 },
    decisions: {},
  };
}

exports.handler = async function(event) {
  if (event.httpMethod !== 'GET') {
    return { statusCode: 405, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  const adminToken = process.env.ADMIN_TOKEN;
  if (!adminToken || event.headers['x-admin-token'] !== adminToken) {
    return { statusCode: 401, body: JSON.stringify({ error: 'Unauthorized' }) };
  }

  try {
    const { sha, json } = await readJsonFile(FILE_PATH);
    const payload = json || emptySeed();
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha, ...payload }),
    };
  } catch (err) {
    return {
      statusCode: 502,
      body: JSON.stringify({ error: err.message || String(err) }),
    };
  }
};
