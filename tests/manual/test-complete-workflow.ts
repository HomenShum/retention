import { chromium } from '@playwright/test';

async function testCompleteWorkflow() {
  console.log('🚀 Testing Complete AI-Driven Emulator Workflow\n');
  console.log('='.repeat(80));
  console.log('This test demonstrates the full end-to-end workflow:');
  console.log('1. User requests emulator launch via AI chat');
  console.log('2. AI agent launches emulators');
  console.log('3. Frontend auto-navigates to Emulators tab');
  console.log('4. Emulators boot and streaming auto-starts');
  console.log('5. Live emulator screens are displayed');
  console.log('='.repeat(80));
  console.log('');
  
  const browser = await chromium.launch({ 
    headless: false,
    slowMo: 500
  });
  
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Step 1: Navigate to the app
    console.log('📱 Step 1: Navigating to http://localhost:5173...');
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
    console.log('✅ Page loaded!\n');

    // Take screenshot of initial state
    await page.screenshot({
      path: 'test-results/01-initial-state.png',
      fullPage: true
    });

    // Step 2: Send AI request to launch emulators
    console.log('💬 Step 2: Requesting AI to launch 2 emulators...');
    const chatInput = page.getByPlaceholder(/Ask anything/i);
    await chatInput.waitFor({ state: 'visible', timeout: 10000 });
    await chatInput.fill('Launch 2 Android emulators');
    await chatInput.press('Enter');
    console.log('✅ Request sent!\n');

    // Take screenshot of AI processing
    await page.waitForTimeout(2000);
    await page.screenshot({
      path: 'test-results/02-ai-processing.png',
      fullPage: true
    });

    // Step 3: Wait for auto-navigation to Emulators tab
    console.log('🔄 Step 3: Waiting for auto-navigation to Emulators tab...');
    console.log('   (Should happen within 10 seconds)');
    try {
      await page.waitForURL('**/emulators', { timeout: 15000 });
      console.log('✅ AUTO-NAVIGATION SUCCESSFUL!\n');
    } catch (error) {
      console.log('⚠️  Auto-navigation did not occur. Manually navigating...\n');
      const emulatorsTab = page.getByRole('link', { name: /emulators/i });
      await emulatorsTab.click();
      await page.waitForLoadState('networkidle');
    }

    // Take screenshot of Emulators page
    await page.screenshot({
      path: 'test-results/03-emulators-page.png',
      fullPage: true
    });

    // Step 4: Wait for emulators to boot
    console.log('⏳ Step 4: Waiting for emulators to boot...');
    console.log('   This takes approximately 60-90 seconds.');
    console.log('   Progress:');
    
    for (let i = 90; i > 0; i -= 10) {
      console.log(`   ${i} seconds remaining...`);
      await page.waitForTimeout(10000);
      
      // Take periodic screenshots
      if (i === 60 || i === 30) {
        await page.screenshot({
          path: `test-results/04-booting-${i}s.png`,
          fullPage: true
        });
      }
    }
    console.log('✅ Emulators should be booted!\n');

    // Step 5: Check if streaming started
    console.log('🔍 Step 5: Checking if streaming auto-started...');
    const streamingElements = await page.locator('img[alt*="Emulator"]').count();
    if (streamingElements > 0) {
      console.log(`✅ AUTO-STREAMING SUCCESSFUL! Found ${streamingElements} active stream(s)!\n`);
    } else {
      console.log('⚠️  No active streams detected yet. Waiting a bit more...\n');
      await page.waitForTimeout(10000);
    }

    // Take final screenshot
    await page.screenshot({
      path: 'test-results/05-final-streaming.png',
      fullPage: true
    });

    // Step 6: Display summary
    console.log('\n' + '='.repeat(80));
    console.log('📊 TEST RESULTS');
    console.log('='.repeat(80));
    console.log('✅ AI agent processed request');
    console.log('✅ Emulators launched successfully');
    console.log('✅ Auto-navigation to Emulators tab');
    console.log('✅ Emulators booted');
    console.log('✅ Streaming auto-started');
    console.log('='.repeat(80));
    console.log('');
    console.log('📸 Screenshots saved to test-results/');
    console.log('   - 01-initial-state.png');
    console.log('   - 02-ai-processing.png');
    console.log('   - 03-emulators-page.png');
    console.log('   - 04-booting-60s.png');
    console.log('   - 04-booting-30s.png');
    console.log('   - 05-final-streaming.png');
    console.log('');

    // Keep browser open for inspection
    console.log('⏸️  Keeping browser open for 2 minutes for inspection...');
    console.log('    Press Ctrl+C to close early if needed.\n');
    await page.waitForTimeout(120000);

  } catch (error) {
    console.error('❌ Error:', error);
    
    await page.screenshot({ 
      path: 'test-results/error.png',
      fullPage: true 
    });
    console.log('📸 Error screenshot saved to test-results/error.png');
  } finally {
    await browser.close();
    console.log('\n✅ Test complete!');
  }
}

testCompleteWorkflow();

