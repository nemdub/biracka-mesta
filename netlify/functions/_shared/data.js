// The slim API data module is built by scripts/generate_api_data.py during
// the Netlify build (see netlify.toml). It exports:
//   * stations:   flat array of polling-station rows with short-key search
//                 fields (n, nl, nr, nc) and rId/cId references.
//   * regions:    id -> { id, name_cyr, name_lat } catalogue.
//   * counties:   id -> { id, name_cyr, name_lat, region_id } catalogue.
//   * localities: id -> { id, name_cyr, name_lat, region_id, county_id }
//                 catalogue. `county_id` is '' for localities in the
//                 `ostalo` bucket (diaspora, prisons, MoD).
//
// Requiring it at module scope means V8 parses + caches the literal during
// Lambda's init phase (which is not billed against request latency); the
// first request to a fresh container then hits an already-built array.
const bundle = require('./api_stations_data');

const stations = bundle.stations;
const regions = bundle.regions;
const counties = bundle.counties;
const localities = bundle.localities;

function loadStations() {
  return stations;
}

function getRegion(id) {
  return id && regions[id] ? regions[id] : null;
}

function getCounty(id) {
  return id && counties[id] ? counties[id] : null;
}

function getLocality(id) {
  return id && localities[id] ? localities[id] : null;
}

// Build the nested regions+counties+localities catalogue exactly once, at
// module load. One pass over stations populates station counts at every
// level; locality -> county / region membership comes from the bundle
// catalogue (so zero-station localities still appear). All arrays are
// sorted by name_lat. The result is frozen and reused on every request —
// handlers just stringify it.
const catalogue = (() => {
  const stationsByRegion = new Map();
  const stationsByCounty = new Map();
  const stationsByLocality = new Map();
  for (const s of stations) {
    if (s.rId) stationsByRegion.set(s.rId, (stationsByRegion.get(s.rId) || 0) + 1);
    if (s.cId) stationsByCounty.set(s.cId, (stationsByCounty.get(s.cId) || 0) + 1);
    if (s.localityId) stationsByLocality.set(s.localityId, (stationsByLocality.get(s.localityId) || 0) + 1);
  }

  const byNameLat = (a, b) => a.name_lat.localeCompare(b.name_lat);

  const toLocalityEntry = (l) => ({
    id: l.id,
    name_cyr: l.name_cyr,
    name_lat: l.name_lat,
    station_count: stationsByLocality.get(l.id) || 0,
  });

  const allLocalities = Object.values(localities);

  const localitiesInCounty = (countyId) =>
    allLocalities
      .filter((l) => l.county_id === countyId)
      .map(toLocalityEntry)
      .sort(byNameLat);

  const uncountiedLocalitiesInRegion = (regionId) =>
    allLocalities
      .filter((l) => !l.county_id && l.region_id === regionId)
      .map(toLocalityEntry)
      .sort(byNameLat);

  const regionsOut = Object.values(regions)
    .slice()
    .sort(byNameLat)
    .map((r) => ({
      id: r.id,
      name_cyr: r.name_cyr,
      name_lat: r.name_lat,
      station_count: stationsByRegion.get(r.id) || 0,
      localities: uncountiedLocalitiesInRegion(r.id),
      counties: Object.values(counties)
        .filter((c) => c.region_id === r.id)
        .sort(byNameLat)
        .map((c) => ({
          id: c.id,
          name_cyr: c.name_cyr,
          name_lat: c.name_lat,
          station_count: stationsByCounty.get(c.id) || 0,
          localities: localitiesInCounty(c.id),
        })),
    }));

  return Object.freeze({ regions: regionsOut });
})();

function getCatalogue() {
  return catalogue;
}

module.exports = { loadStations, getRegion, getCounty, getLocality, getCatalogue };
