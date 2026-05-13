/**
 * POST /.netlify/functions/save-verdicts
 * Header: x-admin-token
 * Body: { decisions: { <key>: { ... } } }
 *
 * Merges incoming decisions into data/coord_verdicts.json on GitHub.
 *
 * Merge rule: for each key in the incoming `decisions`, write it unless the
 * server already has a decision for that key with a strictly newer
 * `decided_at` (in which case the server wins — protects against stale-tab
 * stomping). Returns the merged-and-committed payload so the client can sync
 * its own state back.
 */

const { readJsonFile, writeJsonFile } = require('./_shared/github_file');

const FILE_PATH = 'data/coord_verdicts.json';
const ALLOWED_VERDICTS = new Set(['xlsx', 'json', 'neither']);

function emptySeed() {
  return {
    schema_version: 1,
    updated_at: null,
    decided_by_count: { xlsx: 0, json: 0, neither: 0 },
    decisions: {},
  };
}

function isValidDecision(d) {
  if (!d || typeof d !== 'object') return false;
  if (!ALLOWED_VERDICTS.has(d.verdict)) return false;
  if (typeof d.opstina_cyr !== 'string' || !d.opstina_cyr) return false;
  if (typeof d.decided_at !== 'string' || !d.decided_at) return false;
  return true;
}

function isNewer(a, b) {
  // Both expected to be ISO 8601 strings. Lexicographic compare works for
  // ISO 8601 in UTC.
  if (!a) return false;
  if (!b) return true;
  return a > b;
}

async function mergeAndWrite(incoming) {
  const { sha, json } = await readJsonFile(FILE_PATH);
  const current = json || emptySeed();
  const merged = {
    schema_version: 1,
    updated_at: new Date().toISOString(),
    decisions: { ...current.decisions },
  };

  let written = 0;
  let skipped = 0;
  for (const [key, decision] of Object.entries(incoming || {})) {
    if (!isValidDecision(decision)) {
      skipped++;
      continue;
    }
    const existing = merged.decisions[key];
    if (existing && isNewer(existing.decided_at, decision.decided_at)) {
      skipped++;
      continue;
    }
    merged.decisions[key] = decision;
    written++;
  }

  // Recompute counts
  const counts = { xlsx: 0, json: 0, neither: 0 };
  for (const d of Object.values(merged.decisions)) {
    if (counts[d.verdict] !== undefined) counts[d.verdict]++;
  }
  merged.decided_by_count = counts;

  const total = Object.keys(merged.decisions).length;
  const commitMsg = `chore(verdicts): sync ${written} (total ${total})`;
  const newSha = await writeJsonFile(FILE_PATH, merged, sha, commitMsg);
  return { merged, sha: newSha, written, skipped };
}

exports.handler = async function(event) {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  const adminToken = process.env.ADMIN_TOKEN;
  if (!adminToken || event.headers['x-admin-token'] !== adminToken) {
    return { statusCode: 401, body: JSON.stringify({ error: 'Unauthorized' }) };
  }

  let body;
  try {
    body = JSON.parse(event.body || '{}');
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid JSON body' }) };
  }

  if (!body.decisions || typeof body.decisions !== 'object') {
    return { statusCode: 400, body: JSON.stringify({ error: 'Missing decisions object' }) };
  }

  // One retry if GitHub rejects the SHA (someone else committed in between).
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const result = await mergeAndWrite(body.decisions);
      return {
        statusCode: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ok: true,
          sha: result.sha,
          written: result.written,
          skipped: result.skipped,
          updated_at: result.merged.updated_at,
          decided_by_count: result.merged.decided_by_count,
          total_decisions: Object.keys(result.merged.decisions).length,
        }),
      };
    } catch (err) {
      if (attempt === 0 && (err.status === 409 || err.status === 412 || err.status === 422)) {
        continue; // refresh SHA and retry
      }
      return {
        statusCode: 502,
        body: JSON.stringify({ error: err.message || String(err) }),
      };
    }
  }

  return {
    statusCode: 502,
    body: JSON.stringify({ error: 'GitHub PUT failed after retry' }),
  };
};
