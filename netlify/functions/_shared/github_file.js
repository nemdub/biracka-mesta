/**
 * GitHub file read/write helpers shared by the verdict-sync functions.
 *
 * Uses the Contents API for metadata + commits, but reads file content via
 * the Git Blobs API so we aren't capped at 1 MB.
 */

const GITHUB_API = 'https://api.github.com';

function ghEnv() {
  const token = process.env.GITHUB_TOKEN;
  const owner = process.env.GITHUB_REPO_OWNER;
  const repo  = process.env.GITHUB_REPO_NAME;
  if (!token || !owner || !repo) {
    throw new Error('Missing GITHUB_TOKEN / GITHUB_REPO_OWNER / GITHUB_REPO_NAME');
  }
  return { token, owner, repo };
}

function ghHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

/**
 * Read a file from GitHub. Returns `{ sha, json }` where `json` is the
 * parsed content. If the file does not exist, returns `{ sha: null, json: null }`.
 * Throws on any other error.
 */
async function readJsonFile(filePath) {
  const { token, owner, repo } = ghEnv();
  const url = `${GITHUB_API}/repos/${owner}/${repo}/contents/${filePath}`;
  const metaResp = await fetch(url, { headers: ghHeaders(token) });
  if (metaResp.status === 404) {
    return { sha: null, json: null };
  }
  if (!metaResp.ok) {
    const detail = await metaResp.text();
    throw new Error(`GitHub Contents API ${metaResp.status}: ${detail}`);
  }
  const meta = await metaResp.json();
  const blobResp = await fetch(
    `${GITHUB_API}/repos/${owner}/${repo}/git/blobs/${meta.sha}`,
    { headers: ghHeaders(token) },
  );
  if (!blobResp.ok) {
    const detail = await blobResp.text();
    throw new Error(`GitHub Blobs API ${blobResp.status}: ${detail}`);
  }
  const blob = await blobResp.json();
  const decoded = Buffer.from(blob.content, 'base64').toString('utf8');
  return { sha: meta.sha, json: JSON.parse(decoded) };
}

/**
 * Write a JSON file via the Contents API. Pass `sha` for an existing file,
 * or null/undefined to create a new one. Returns the new blob SHA on success.
 * Throws on error (including 409/412 SHA mismatch).
 */
async function writeJsonFile(filePath, json, sha, commitMessage) {
  const { token, owner, repo } = ghEnv();
  const url = `${GITHUB_API}/repos/${owner}/${repo}/contents/${filePath}`;
  const content = Buffer.from(JSON.stringify(json, null, 2) + '\n').toString('base64');
  const body = {
    message: commitMessage,
    content,
  };
  if (sha) body.sha = sha;
  const resp = await fetch(url, {
    method: 'PUT',
    headers: { ...ghHeaders(token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    const err = new Error(`GitHub PUT ${resp.status}: ${detail}`);
    err.status = resp.status;
    throw err;
  }
  const data = await resp.json();
  return data.content && data.content.sha;
}

module.exports = { readJsonFile, writeJsonFile };
