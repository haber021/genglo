// Learn more https://docs.expo.dev/guides/customizing-metro
const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

/** @type {import('expo/metro-config').MetroConfig} */
const config = getDefaultConfig(__dirname);

// Optimize memory usage by reducing workers
// This helps prevent out-of-memory errors during bundling
config.maxWorkers = 1;

// Configure cache to use less disk space
// Metro will use a more conservative caching strategy
config.resetCache = false;

// Reduce watchman cache size
config.watchFolders = [path.resolve(__dirname)];

module.exports = config;

