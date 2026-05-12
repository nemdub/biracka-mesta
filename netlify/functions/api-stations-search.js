/**
 * GET /api/stations/search?q=<query>&limit=<n>
 *
 * Substring match (script + diacritic insensitive) against polling station
 * name OR locality name. Returns both mapped and unmapped stations; unmapped
 * stations have geo.lat = null and geo.lon = null.
 *
 * Requires X-Api-Key header matching one of the keys in API_KEYS env var.
 */
const { authenticate } = require('./_shared/auth');
const { loadStations } = require('./_shared/data');
const { normalize } = require('./_shared/translit');
const { ok, err } = require('./_shared/respond');

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;
const MIN_QUERY_LEN = 2;
const MAX_QUERY_LEN = 64;

exports.handler = async function (event) {
  if (event.httpMethod === 'OPTIONS') {
    return err(405, 'METHOD_NOT_ALLOWED', 'Method not allowed');
  }
  if (event.httpMethod !== 'GET') {
    return err(405, 'METHOD_NOT_ALLOWED', 'Method not allowed');
  }

  const clientId = authenticate(event);
  if (!clientId) return err(401, 'UNAUTHORIZED', 'Unauthorized');

  const params = event.queryStringParameters || {};
  const rawQ = (params.q || '').trim();
  if (rawQ.length > MAX_QUERY_LEN) {
    return err(400, 'BAD_REQUEST', `Query "q" must be at most ${MAX_QUERY_LEN} characters`);
  }
  const normQ = normalize(rawQ);
  if (normQ.length < MIN_QUERY_LEN) {
    return err(400, 'BAD_REQUEST', `Query "q" must be at least ${MIN_QUERY_LEN} characters after normalization`);
  }

  let limit = parseInt(params.limit, 10);
  if (!Number.isFinite(limit) || limit <= 0) limit = DEFAULT_LIMIT;
  if (limit > MAX_LIMIT) limit = MAX_LIMIT;

  let stations;
  try {
    stations = loadStations();
  } catch (e) {
    console.error('[search] data load failed:', e.message);
    return err(500, 'INTERNAL_ERROR', 'Failed to load data');
  }

  const results = [];
  for (const s of stations) {
    if (s.n.includes(normQ) || s.nl.includes(normQ)) {
      results.push({
        id: s.id,
        name: s.name,
        locality: { id: s.localityId, name: s.localityName },
        geo: { lat: s.lat, lon: s.lon },
      });
      if (results.length >= limit) break;
    }
  }

  console.log(`[search] client=${clientId} q="${rawQ}" matches=${results.length}`);

  return ok({ count: results.length, results });
};
