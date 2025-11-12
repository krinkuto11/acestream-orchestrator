#!/usr/bin/env node

/**
 * Generate version.json file for the UI
 * This file contains the current version from package.json and build timestamp
 */

const fs = require('fs');
const path = require('path');

// Read version from package.json
const packageJson = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8')
);

// Create version info object
const versionInfo = {
  version: packageJson.version,
  buildTime: new Date().toISOString(),
  buildTimestamp: Date.now()
};

// Write to public directory so it's included in the build
const publicDir = path.join(__dirname, 'public');
if (!fs.existsSync(publicDir)) {
  fs.mkdirSync(publicDir, { recursive: true });
}

const outputPath = path.join(publicDir, 'version.json');
fs.writeFileSync(outputPath, JSON.stringify(versionInfo, null, 2));

console.log('Version file generated:', outputPath);
console.log('Version:', versionInfo.version);
console.log('Build time:', versionInfo.buildTime);
