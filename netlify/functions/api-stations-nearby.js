/**
 * GET /api/stations/nearby?lat=<lat>&lon=<lon>&limit=<n>&max_distance_m=<m>
 *
 * Returns the `limit` polling stations closest to (lat, lon), sorted by
 * ascending distance. Unmapped stations are excluded. If max_distance_m is
 * provided, stations farther away are dropped.
 *
 * Coordinates must fall inside Serbia's bounding box; out-of-area calls are
 * rejected with 400 to bound origin work and avoid CDN cache pollution.
 *
 * Requires X-Api-Key header matching one of the keys in API_KEYS env var.
 */
const { authenticate } = require('./_shared/auth');
const { loadStations } = require('./_shared/data');
const { ok, err } = require('./_shared/respond');

const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 100;
const EARTH_RADIUS_M = 6371000;
const DEG_TO_RAD = Math.PI / 180;

// Serbia bounding box (slightly padded). Inputs outside this box are rejected.
const SERBIA_LAT_MIN = 42.0;
const SERBIA_LAT_MAX = 47.0;
const SERBIA_LON_MIN = 18.0;
const SERBIA_LON_MAX = 23.5;

function haversineMeters(lat1, lon1, lat2, lon2) {
  const dLat = (lat2 - lat1) * DEG_TO_RAD;
  const dLon = (lon2 - lon1) * DEG_TO_RAD;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * DEG_TO_RAD) * Math.cos(lat2 * DEG_TO_RAD) *
      Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
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
  const lat = parseFloat(params.lat);
  const lon = parseFloat(params.lon);
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    return err(400, 'BAD_REQUEST', 'Param "lat" must be a number in [-90, 90]');
  }
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
    return err(400, 'BAD_REQUEST', 'Param "lon" must be a number in [-180, 180]');
  }
  if (lat < SERBIA_LAT_MIN || lat > SERBIA_LAT_MAX ||
      lon < SERBIA_LON_MIN || lon > SERBIA_LON_MAX) {
    return err(400, 'BAD_REQUEST', 'Coordinate is outside the service area');
  }

  let limit = parseInt(params.limit, 10);
  if (!Number.isFinite(limit) || limit <= 0) limit = DEFAULT_LIMIT;
  if (limit > MAX_LIMIT) limit = MAX_LIMIT;

  let maxDistanceM = null;
  if (params.max_distance_m != null && params.max_distance_m !== '') {
    const m = parseInt(params.max_distance_m, 10);
    if (!Number.isFinite(m) || m <= 0) {
      return err(400, 'BAD_REQUEST', 'Param "max_distance_m" must be a positive integer');
    }
    maxDistanceM = m;
  }

  let stations;
  try {
    stations = loadStations();
  } catch (e) {
    console.error('[nearby] data load failed:', e.message);
    return err(500, 'INTERNAL_ERROR', 'Failed to load data');
  }

  // Cheap bounding-box prefilter when max_distance_m is set.
  let dLatMax = Infinity;
  let dLonMax = Infinity;
  if (maxDistanceM != null) {
    dLatMax = maxDistanceM / 111320;
    const cosLat = Math.cos(lat * DEG_TO_RAD);
    dLonMax = cosLat > 1e-6 ? maxDistanceM / (111320 * cosLat) : Infinity;
  }

  const scored = [];
  for (const s of stations) {
    if (s.lat == null) continue;
    if (maxDistanceM != null) {
      if (Math.abs(s.lat - lat) > dLatMax) continue;
      if (Math.abs(s.lon - lon) > dLonMax) continue;
    }
    const d = haversineMeters(lat, lon, s.lat, s.lon);
    if (maxDistanceM != null && d > maxDistanceM) continue;
    scored.push({ s, d });
  }

  scored.sort((a, b) => a.d - b.d);
  const top = scored.slice(0, limit);

  const results = top.map(({ s, d }) => ({
    id: s.id,
    name: s.name,
    locality: { id: s.localityId, name: s.localityName },
    geo: { lat: s.lat, lon: s.lon },
    distance_m: Math.round(d),
  }));

  console.log(`[nearby] client=${clientId} lat=${lat} lon=${lon} results=${results.length}`);

  return ok({ count: results.length, results });
};
