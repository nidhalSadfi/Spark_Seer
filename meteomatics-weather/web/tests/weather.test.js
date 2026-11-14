import test from 'node:test';
import assert from 'node:assert/strict';
import { colour, layers, formatValue } from '../src/weather.js';

test('missing weather has a distinct colour and is never displayed as zero', () => {
  assert.equal(formatValue(null, 'mm'), 'Unavailable');
  assert.equal(formatValue(0, 'mm'), '0 mm');
  assert.equal(colour(null, layers.rainfall), '#a3ada8');
  assert.notEqual(colour(0, layers.rainfall), colour(null, layers.rainfall));
});

test('layer colours use explicit units and stable bins', () => {
  assert.equal(layers.wind.field, 'max_wind_speed_kmh');
  assert.equal(colour(10, layers.temperature), layers.temperature.colours[1]);
  assert.equal(colour(45, layers.temperature), layers.temperature.colours.at(-1));
});
