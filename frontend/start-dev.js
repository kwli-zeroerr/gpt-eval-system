#!/usr/bin/env node

// Display figlet banner before starting Vite
import figlet from 'figlet';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Display figlet banner
figlet('Now You See it', { font: 'Standard' }, (err, data) => {
  if (err) {
    console.log('\n=== Now You See it ===\n');
  } else {
    console.log('\n' + data + '\n');
  }
  console.log('='.repeat(60));
  console.log('RAG Evaluation System - Frontend Server');
  console.log('='.repeat(60) + '\n');
  
  // Start Vite using the vite.config.ts
  const viteProcess = spawn('npx', ['vite'], {
    cwd: __dirname,
    stdio: 'inherit',
    shell: true
  });
  
  viteProcess.on('error', (error) => {
    console.error('Failed to start Vite:', error);
    process.exit(1);
  });
  
  viteProcess.on('exit', (code) => {
    process.exit(code || 0);
  });
});
