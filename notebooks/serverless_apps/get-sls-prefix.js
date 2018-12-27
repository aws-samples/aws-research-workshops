const { readFileSync } = require('fs');

const json = readFileSync('./dist/serverless-state.json');
const state = JSON.parse(json);

const prefix = state.package.artifactDirectoryName;
const bucket = state.service.provider.deploymentBucket;

console.log(`s3://${bucket}/${prefix}`);