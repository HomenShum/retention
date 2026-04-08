import { chromium } from 'playwright';

async function testChatSync() {
  console.log('🔄 Testing Chat Sync Between Home Page and AI Chat Page');
  console.log('='.repeat(60));

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Test 1: Home Page CollapsibleChat
    console.log('\n📱 Test 1: Home Page CollapsibleChat');
    console.log('-'.repeat(60));
    
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
    
    // Wait for chat to be visible
    await page.waitForSelector('text=AI Assistant', { timeout: 5000 });
    console.log('✅ Home page loaded with AI Assistant chat');
    
    // Take screenshot
    await page.screenshot({ path: 'test-results/chat-sync-01-home.png', fullPage: true });
    console.log('✅ Screenshot saved: chat-sync-01-home.png');
    
    // Send a test message with markdown
    const homeTextarea = await page.locator('textarea[placeholder*="Ask"]').first();
    await homeTextarea.fill('**Test markdown** on home page:\n\n1. First item\n2. Second item\n\n`code block`');
    console.log('✅ Typed markdown message in home page chat');
    
    await page.screenshot({ path: 'test-results/chat-sync-02-home-typed.png', fullPage: true });
    
    // Click send button
    const homeSendButton = await page.locator('button:has(svg.lucide-send)').first();
    await homeSendButton.click();
    console.log('✅ Sent message on home page');
    
    // Wait for response
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/chat-sync-03-home-response.png', fullPage: true });
    console.log('✅ Screenshot saved: chat-sync-03-home-response.png');
    
    // Test 2: AI Chat Page
    console.log('\n📱 Test 2: AI Chat Page (Dedicated Route)');
    console.log('-'.repeat(60));
    
    await page.goto('http://localhost:5173/ai-chat');
    await page.waitForLoadState('networkidle');
    
    // Wait for chat to be visible
    await page.waitForSelector('text=AI Assistant', { timeout: 5000 });
    console.log('✅ AI Chat page loaded');
    
    await page.screenshot({ path: 'test-results/chat-sync-04-ai-chat.png', fullPage: true });
    console.log('✅ Screenshot saved: chat-sync-04-ai-chat.png');
    
    // Send a test message with markdown
    const aiChatTextarea = await page.locator('textarea[placeholder*="Type your message"]').first();
    await aiChatTextarea.fill('**Test markdown** on AI chat page:\n\n- Bullet 1\n- Bullet 2\n\n> Blockquote test');
    console.log('✅ Typed markdown message in AI chat page');
    
    await page.screenshot({ path: 'test-results/chat-sync-05-ai-chat-typed.png', fullPage: true });
    
    // Click send button
    const aiChatSendButton = await page.locator('button:has(svg.lucide-send)').first();
    await aiChatSendButton.click();
    console.log('✅ Sent message on AI chat page');
    
    // Wait for response
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/chat-sync-06-ai-chat-response.png', fullPage: true });
    console.log('✅ Screenshot saved: chat-sync-06-ai-chat-response.png');
    
    // Test 3: Sidebar Navigation
    console.log('\n📱 Test 3: Sidebar Navigation');
    console.log('-'.repeat(60));
    
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
    
    // Click AI Chat in sidebar
    await page.click('text=AI Chat');
    await page.waitForLoadState('networkidle');
    console.log('✅ Navigated to AI Chat via sidebar');
    
    await page.screenshot({ path: 'test-results/chat-sync-07-sidebar-nav.png', fullPage: true });
    console.log('✅ Screenshot saved: chat-sync-07-sidebar-nav.png');
    
    // Verify we're on the AI Chat page
    const url = page.url();
    if (url.includes('/ai-chat')) {
      console.log('✅ Successfully navigated to /ai-chat route');
    } else {
      console.log('❌ Navigation failed - current URL:', url);
    }
    
    console.log('\n' + '='.repeat(60));
    console.log('✅ Chat sync test complete!');
    console.log('\nScreenshots saved:');
    console.log('  - test-results/chat-sync-01-home.png');
    console.log('  - test-results/chat-sync-02-home-typed.png');
    console.log('  - test-results/chat-sync-03-home-response.png');
    console.log('  - test-results/chat-sync-04-ai-chat.png');
    console.log('  - test-results/chat-sync-05-ai-chat-typed.png');
    console.log('  - test-results/chat-sync-06-ai-chat-response.png');
    console.log('  - test-results/chat-sync-07-sidebar-nav.png');
    console.log('\nPlease review the screenshots to verify:');
    console.log('  1. Both chats render markdown properly');
    console.log('  2. Styling is consistent between both chats');
    console.log('  3. Sidebar navigation works correctly');
    
  } catch (error) {
    console.error('❌ Test failed:', error);
    await page.screenshot({ path: 'test-results/chat-sync-error.png', fullPage: true });
    console.log('Error screenshot saved: chat-sync-error.png');
  } finally {
    await browser.close();
  }
}

testChatSync();

