// Run with: npm run test:electron
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const { pathToFileURL } = require('url');
const { recoverableFolderHash, DEFAULT_HASH } = require('../reload-recovery');

const INDEX = path.resolve('/app/resources/app.asar/dist/frontend/browser/index.html');
const folderUrl = (hash = '') => pathToFileURL(path.dirname(INDEX)).href + '/' + hash;

test('reload of the app folder recovers on the same route', () => {
  assert.equal(recoverableFolderHash(folderUrl('#/interview'), INDEX), '/interview');
  assert.equal(recoverableFolderHash(folderUrl('#/login?redirect=%2Fsettings'), INDEX), '/login?redirect=%2Fsettings');
});

test('reload of the app folder without a route lands on the default screen', () => {
  assert.equal(recoverableFolderHash(folderUrl(), INDEX), DEFAULT_HASH);
});

test('desktop build keeps the address on index.html so a reload never hits the folder (black screen)', () => {
  const angular = require('../../angular.json');
  const project = Object.values(angular.projects)[0];
  const href = project.architect.build.configurations.electron.baseHref;
  assert.equal(href, './index.html');
  // Angular's hash router resolves '#/route' against the base URL.
  const base = pathToFileURL(INDEX).href;
  assert.equal(new URL('#/interview', base).href, base + '#/interview');
});

test('other failed loads are left alone (no reload loop, no hijacking)', () => {
  assert.equal(recoverableFolderHash(pathToFileURL(INDEX).href + '#/interview', INDEX), null);
  assert.equal(recoverableFolderHash('https://www.oyeinterview.com/#/interview', INDEX), null);
  assert.equal(recoverableFolderHash('http://localhost:4200/', INDEX), null);
  assert.equal(recoverableFolderHash(pathToFileURL(path.resolve('/somewhere/else')).href + '/', INDEX), null);
  assert.equal(recoverableFolderHash('not a url', INDEX), null);
});
