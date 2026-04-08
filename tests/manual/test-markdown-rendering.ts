import { chromium } from 'playwright';

async function testMarkdownRendering() {
  console.log('🎨 Testing Markdown Rendering in AI Chat');
  console.log('==========================================\n');

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Navigate to the AI chat page
    console.log('📱 Navigating to http://localhost:5173/ai-chat...');
    await page.goto('http://localhost:5173/ai-chat');
    await page.waitForLoadState('networkidle');
    
    // Take initial screenshot
    await page.screenshot({ path: 'test-results/markdown-01-initial.png', fullPage: true });
    console.log('✅ Initial screenshot saved\n');

    // Find the chat input
    console.log('💬 Sending test message with markdown...');
    const chatInput = page.locator('textarea[placeholder*="Type your message"]').first();
    await chatInput.waitFor({ state: 'visible', timeout: 10000 });
    
    // Type a message with markdown formatting
    const testMessage = `Test markdown formatting:
**Bold text**
*Italic text*
- List item 1
- List item 2
- List item 3

1. Numbered item 1
2. Numbered item 2

\`inline code\`

\`\`\`
code block
multiple lines
\`\`\`

> Blockquote text

---

# Heading 1
## Heading 2
### Heading 3
`;
    
    await chatInput.fill(testMessage);
    await page.screenshot({ path: 'test-results/markdown-02-typed.png', fullPage: true });
    console.log('✅ Message typed\n');

    // Send the message
    await chatInput.press('Enter');
    console.log('✅ Message sent\n');

    // Wait for AI response
    console.log('⏳ Waiting for AI response...');
    await page.waitForTimeout(5000);
    
    // Take screenshot of the response
    await page.screenshot({ path: 'test-results/markdown-03-response.png', fullPage: true });
    console.log('✅ Response screenshot saved\n');

    // Wait a bit more to see the full conversation
    await page.waitForTimeout(3000);
    await page.screenshot({ path: 'test-results/markdown-04-final.png', fullPage: true });
    console.log('✅ Final screenshot saved\n');

    console.log('==========================================');
    console.log('✅ Markdown rendering test complete!');
    console.log('\nScreenshots saved:');
    console.log('  - test-results/markdown-01-initial.png');
    console.log('  - test-results/markdown-02-typed.png');
    console.log('  - test-results/markdown-03-response.png');
    console.log('  - test-results/markdown-04-final.png');
    console.log('\nPlease review the screenshots to verify markdown rendering.');

  } catch (error) {
    console.error('❌ Error:', error);
    await page.screenshot({ path: 'test-results/markdown-error.png', fullPage: true });
  } finally {
    await browser.close();
  }
}

testMarkdownRendering();

