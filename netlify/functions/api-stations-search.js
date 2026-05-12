/**
 * GET /api/stations/search?q=<query>&limit=<n>
 *
 * Substring match (script + diacritic insensitive) against polling station
 * name OR community name. Returns mapped stations only.
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

exports.handler = async function (event) {
  if (event.httpMethod !== 'GET') {
    return err(405, 'METHOD_NOT_ALLOWED', 'Method not allowed');
  }

  const clientId = authenticate(event);
  if (!clientId) return err(401, 'UNAUTHORIZED', 'Unauthorized');

  const params = event.queryStringParameters || {};
  const rawQ = (params.q || '').trim();
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
    if (s._norm.includes(normQ) || s._normCommunity.includes(normQ)) {
      results.push({
        id: s.id,
        name: s.name,
        community: { id: s.communityId, name: s.communityName },
        geo: { lat: s.lat, lon: s.lon },
      });
      if (results.length >= limit) break;
    }
  }

  console.log(`[search] client=${clientId} q="${rawQ}" matches=${results.length}`);

  return ok({ count: results.length, results });
};
