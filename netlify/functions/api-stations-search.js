/**
 * GET /api/stations/search?q=<query>&limit=<n>&region=<id>&county=<id>
 *
 * Substring match (script + diacritic insensitive) against polling station
 * name, locality name, region name, or county name. Returns both mapped and
 * unmapped stations; unmapped stations have geo.lat = null and geo.lon = null.
 *
 * Optional `region` and `county` query params pre-filter the result set by
 * canonical id (see data/serbia_admin.json for the catalogue).
 *
 * Requires X-Api-Key header matching one of the keys in API_KEYS env var.
 */
const { authenticate } = require('./_shared/auth');
const { loadStations, getRegion, getCounty, getLocality } = require('./_shared/data');
const { normalize } = require('./_shared/translit');
const { ok, err } = require('./_shared/respond');

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;
const MIN_QUERY_LEN = 2;
const MAX_QUERY_LEN = 64;

function buildLocality(s) {
  const locality = getLocality(s.localityId);
  const region = getRegion(s.rId);
  const county = getCounty(s.cId);
  return {
    id: s.localityId,
    name_cyr: locality ? locality.name_cyr : null,
    name_lat: locality ? locality.name_lat : null,
    region: region ? { id: region.id, name_cyr: region.name_cyr, name_lat: region.name_lat } : null,
    county: county ? { id: county.id, name_cyr: county.name_cyr, name_lat: county.name_lat } : null,
  };
}

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

  const regionFilter = (params.region || '').trim().toLowerCase() || null;
  if (regionFilter && !getRegion(regionFilter)) {
    return err(400, 'BAD_REQUEST', `Unknown region "${regionFilter}"`);
  }
  const countyFilter = (params.county || '').trim().toLowerCase() || null;
  if (countyFilter && !getCounty(countyFilter)) {
    return err(400, 'BAD_REQUEST', `Unknown county "${countyFilter}"`);
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

  let total = 0;
  const results = [];
  for (const s of stations) {
    if (regionFilter && s.rId !== regionFilter) continue;
    if (countyFilter && s.cId !== countyFilter) continue;
    if (s.n.includes(normQ) || s.nl.includes(normQ) || s.nr.includes(normQ) || s.nc.includes(normQ)) {
      total++;
      if (results.length < limit) {
        results.push({
          id: s.id,
          name_cyr: s.name_cyr,
          name_lat: s.name_lat,
          locality: buildLocality(s),
          geo: { lat: s.lat, lon: s.lon },
        });
      }
    }
  }

  console.log(`[search] client=${clientId} q="${rawQ}" region=${regionFilter || '-'} county=${countyFilter || '-'} total=${total} returned=${results.length}`);

  return ok({ count: total, results });
};
