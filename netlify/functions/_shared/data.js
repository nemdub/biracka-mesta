// The slim API data module is built by scripts/generate_api_data.py during
// the Netlify build (see netlify.toml). It exports:
//   * stations: flat array of polling-station rows with short-key search
//               fields (n, nl, nr, nc) and rId/cId references.
//   * regions:  id -> { id, name_cyr, name_lat } catalogue.
//   * counties: id -> { id, name_cyr, name_lat, region_id } catalogue.
//
// Requiring it at module scope means V8 parses + caches the literal during
// Lambda's init phase (which is not billed against request latency); the
// first request to a fresh container then hits an already-built array.
const bundle = require('./api_stations_data');

const stations = bundle.stations;
const regions = bundle.regions;
const counties = bundle.counties;

function loadStations() {
  return stations;
}

function getRegion(id) {
  return id && regions[id] ? regions[id] : null;
}

function getCounty(id) {
  return id && counties[id] ? counties[id] : null;
}

module.exports = { loadStations, getRegion, getCounty };
