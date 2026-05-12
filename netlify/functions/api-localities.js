/**
 * GET /api/localities
 *
 * Returns the regions + counties catalogue used by the `region` and `county`
 * filter parameters on /api/stations/search. Counties are nested under their
 * parent region; each entry includes a `station_count` (mapped + unmapped).
 *
 * Requires X-Api-Key header matching one of the keys in API_KEYS env var.
 */
const { authenticate } = require('./_shared/auth');
const { getCatalogue } = require('./_shared/data');
const { ok, err } = require('./_shared/respond');

exports.handler = async function (event) {
  if (event.httpMethod !== 'GET') {
    return err(405, 'METHOD_NOT_ALLOWED', 'Method not allowed');
  }

  const clientId = authenticate(event);
  if (!clientId) return err(401, 'UNAUTHORIZED', 'Unauthorized');

  let catalogue;
  try {
    catalogue = getCatalogue();
  } catch (e) {
    console.error('[localities] catalogue load failed:', e.message);
    return err(500, 'INTERNAL_ERROR', 'Failed to load data');
  }

  console.log(`[localities] client=${clientId} regions=${catalogue.regions.length}`);

  return ok(catalogue);
};
