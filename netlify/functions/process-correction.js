/**
 * POST /.netlify/functions/process-correction
 * Body: { submissionId, action, stationId, communityId, newLat, newLon, isApprox }
 * action: "accept" | "reject"
 *
 * On accept:
 *   1. Fetches polling_stations_86.json from GitHub
 *   2. Updates geo for the matching station
 *   3. Commits updated file back to GitHub (triggers Netlify rebuild)
 *   4. Deletes Netlify Forms submission
 *
 * On reject:
 *   1. Deletes Netlify Forms submission only
 */

const GITHUB_API = 'https://api.github.com';
const NETLIFY_API = 'https://api.netlify.com/api/v1';

exports.handler = async function(event) {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  // Auth
  const adminToken = process.env.ADMIN_TOKEN;
  if (!adminToken || event.headers['x-admin-token'] !== adminToken) {
    return { statusCode: 401, body: JSON.stringify({ error: 'Unauthorized' }) };
  }

  // Parse body
  let body;
  try {
    body = JSON.parse(event.body);
  } catch {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid JSON body' }) };
  }

  const { submissionId, action, stationId, communityId, newLat, newLon, isApprox } = body;

  if (!submissionId || !action) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Missing submissionId or action' }) };
  }
  if (action !== 'accept' && action !== 'reject') {
    return { statusCode: 400, body: JSON.stringify({ error: 'action must be "accept" or "reject"' }) };
  }

  // Env vars
  const netlifyToken = process.env.NETLIFY_ACCESS_TOKEN;
  const githubToken  = process.env.GITHUB_TOKEN;
  const repoOwner    = process.env.GITHUB_REPO_OWNER;
  const repoName     = process.env.GITHUB_REPO_NAME;

  if (!netlifyToken) {
    return { statusCode: 500, body: JSON.stringify({ error: 'Missing NETLIFY_ACCESS_TOKEN' }) };
  }

  // ── ACCEPT ──────────────────────────────────────────────────────────────────
  if (action === 'accept') {
    if (!githubToken || !repoOwner || !repoName) {
      return { statusCode: 500, body: JSON.stringify({ error: 'Missing GitHub env vars' }) };
    }
    if (!stationId || !communityId || !newLat || !newLon) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Missing stationId, communityId, newLat, or newLon' }) };
    }

    const lat = parseFloat(newLat);
    const lon = parseFloat(newLon);
    if (isNaN(lat) || isNaN(lon)) {
      return { statusCode: 400, body: JSON.stringify({ error: 'newLat/newLon are not valid numbers' }) };
    }

    // 1. Fetch current file from GitHub
    const filePath = 'polling_stations_86.json';
    const ghFileUrl = `${GITHUB_API}/repos/${repoOwner}/${repoName}/contents/${filePath}`;

    let fileResp;
    try {
      fileResp = await fetch(ghFileUrl, {
        headers: {
          Authorization: `Bearer ${githubToken}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
        },
      });
    } catch (err) {
      return { statusCode: 502, body: JSON.stringify({ error: 'GitHub fetch failed: ' + err.message }) };
    }

    if (!fileResp.ok) {
      const text = await fileResp.text();
      return { statusCode: 502, body: JSON.stringify({ error: `GitHub API error ${fileResp.status}`, detail: text }) };
    }

    const fileJson = await fileResp.json();
    const fileSha  = fileJson.sha;

    // 2. Decode and parse JSON
    let jsonData;
    try {
      const decoded = Buffer.from(fileJson.content, 'base64').toString('utf8');
      jsonData = JSON.parse(decoded);
    } catch (err) {
      return { statusCode: 500, body: JSON.stringify({ error: 'Failed to parse JSON from GitHub: ' + err.message }) };
    }

    // 3. Update the station
    // Structure: { "election": {...}, "communities": [ { id: "1", name: "...", polling_stations: [...] }, ... ] }
    const communities = jsonData.communities;
    if (!Array.isArray(communities)) {
      return { statusCode: 500, body: JSON.stringify({ error: 'Unexpected JSON structure: missing communities array' }) };
    }

    let updated = false;
    for (const comm of communities) {
      if (comm.id !== String(communityId)) continue;
      for (const station of comm.polling_stations) {
        if (station.id !== String(stationId)) continue;
        station.geo         = { lat, lon };
        station.geo_approx  = isApprox === true;
        station.geo_source  = 'volunteer';
        updated = true;
        break;
      }
      if (updated) break;
    }

    if (!updated) {
      return { statusCode: 404, body: JSON.stringify({ error: `Station ${stationId} not found in community ${communityId}` }) };
    }

    // 4. Re-encode and commit
    const newContent = Buffer.from(JSON.stringify(jsonData, null, 2)).toString('base64');
    let putResp;
    try {
      putResp = await fetch(ghFileUrl, {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${githubToken}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: `Apply volunteer correction for station ${stationId}`,
          content: newContent,
          sha: fileSha,
        }),
      });
    } catch (err) {
      return { statusCode: 502, body: JSON.stringify({ error: 'GitHub PUT failed: ' + err.message }) };
    }

    if (!putResp.ok) {
      const text = await putResp.text();
      return { statusCode: 502, body: JSON.stringify({ error: `GitHub PUT error ${putResp.status}`, detail: text }) };
    }
  }

  // ── DELETE SUBMISSION (both accept & reject) ─────────────────────────────
  try {
    const delResp = await fetch(`${NETLIFY_API}/submissions/${submissionId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${netlifyToken}` },
    });
    // 404 is fine (already deleted); anything else is an error
    if (!delResp.ok && delResp.status !== 404) {
      const text = await delResp.text();
      return {
        statusCode: 502,
        body: JSON.stringify({ error: `Failed to delete submission: ${delResp.status}`, detail: text }),
      };
    }
  } catch (err) {
    return { statusCode: 502, body: JSON.stringify({ error: 'Netlify delete failed: ' + err.message }) };
  }

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ok: true, action }),
  };
};
