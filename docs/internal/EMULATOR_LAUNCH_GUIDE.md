# 🚀 Emulator Launch Guide

## Two Ways to Launch Multiple Emulators

---

## **Option 1: Launch via AI Chat** ✅ **NOW AVAILABLE**

### **How It Works:**

The AI agent can now launch emulators directly using the `launch_emulators` tool.

### **Usage Examples:**

```
User: "Launch 5 emulators"
Agent: *Calls launch_emulators(count=5)*
       ✅ Successfully launched 5 emulator(s)
       - emulator-5554
       - emulator-5556
       - emulator-5558
       - emulator-5560
       - emulator-5562

User: "Start 3 Android emulators"
Agent: *Calls launch_emulators(count=3)*
       ✅ Launched 3 emulators

User: "Launch an emulator and wait for it to boot"
Agent: *Calls launch_emulators(count=1, wait_for_boot=True)*
       ✅ Emulator launched and ready
```

### **What Happens Behind the Scenes:**

1. **User sends message** → "Launch 5 emulators"
2. **Coordinator Agent** → Delegates to Device Testing Specialist
3. **Device Testing Specialist** → Calls `launch_emulators(count=5)`
4. **Backend API** → `POST /api/device-simulation/emulators/launch?count=5`
5. **Emulator Manager** → Launches 5 emulators with smart AVD selection
6. **Agent Response** → Returns list of launched device IDs

### **Technical Flow:**

<augment_code_snippet path="backend/app/agents/device_testing/tools/device_testing_tools.py" mode="EXCERPT">
```python
async def launch_emulators(count: int = 1, avd_name: str = None, wait_for_boot: bool = False) -> str:
    """Launch Android emulator(s)."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            "http://localhost:8000/api/device-simulation/emulators/launch",
            params={"count": min(max(count, 1), 20), "wait_for_boot": wait_for_boot}
        )
        # Returns JSON with launched device IDs
```
</augment_code_snippet>

---

## **Option 2: Launch via Emulator Page** ✅ **FULLY WORKING**

### **How It Works:**

Navigate to the Emulator Streaming page and use the "Launch Emulator" button.

### **Step-by-Step:**

1. **Navigate to** → http://localhost:5173/emulators
2. **Click** → "Launch Emulator" button (top-right)
3. **Enter count** → Number of emulators (1-20)
4. **Click** → "Launch" in the dialog
5. **Wait** → Emulators will appear in the device list

### **UI Features:**

- **Launch Dialog** - Specify count (1-20)
- **Placeholder Cards** - Show "Launching..." status
- **Auto-Refresh** - Device list updates when emulators boot
- **Toast Notifications** - Success/error feedback
- **Visual Feedback** - Loading spinners and status indicators

### **Technical Flow:**

<augment_code_snippet path="frontend/test-studio/src/pages/EmulatorStreamingPage.tsx" mode="EXCERPT">
```typescript
const launchEmulators = async () => {
  setLaunching(true)
  
  // Create placeholder devices
  const newDevices: Device[] = Array(emulatorCount).fill(0).map((_, i) => ({
    device_id: `launching-${Date.now()}-${i}`,
    status: 'busy',
    isPlaceholder: true
  }))
  
  setDevices(prev => [...prev, ...newDevices])
  
  // Call backend API
  const response = await fetch(
    `http://localhost:8000/api/device-simulation/emulators/launch?count=${emulatorCount}`,
    { method: 'POST' }
  )
  
  toast.success(`Launching ${emulatorCount} emulators...`)
}
```
</augment_code_snippet>

---

## **Backend Implementation**

### **API Endpoint:**

<augment_code_snippet path="backend/app/api/device_simulation.py" mode="EXCERPT">
```python
@router.post("/emulators/launch")
async def launch_emulator(
    avd_name: Optional[str] = Query(None),
    count: int = Query(1, ge=1, le=20),
    no_snapshot: bool = Query(False),
    wipe_data: bool = Query(False),
    wait_for_boot: bool = Query(False)
):
    """Launch Android emulator(s) with smart AVD selection."""
    # Smart AVD selection - cycles through available AVDs
    # Launches with -gpu swiftshader_indirect for compatibility
    # Uses -no-snapshot to avoid black screen issues
    # Returns list of launched device IDs
```
</augment_code_snippet>

### **Launch Command:**

```bash
emulator -avd Pixel_5_API_30 \
  -port 5554 \
  -no-audio \
  -gpu swiftshader_indirect \
  -no-snapshot
```

### **Smart Features:**

- ✅ **Auto AVD Selection** - Cycles through available AVDs
- ✅ **Port Management** - Auto-assigns ports (5554, 5556, 5558...)
- ✅ **Read-Only Mode** - Uses `-read-only` for multiple instances of same AVD
- ✅ **Black Screen Fix** - Uses `-gpu swiftshader_indirect` and `-no-snapshot`
- ✅ **Concurrent Launch** - Launches multiple emulators with 3s delay between each
- ✅ **Error Handling** - Returns partial success if some emulators fail

---

## **Comparison**

| Feature | AI Chat | Emulator Page |
|---------|---------|---------------|
| **Ease of Use** | ⭐⭐⭐⭐⭐ Natural language | ⭐⭐⭐⭐ Click button |
| **Speed** | ⭐⭐⭐⭐⭐ Instant | ⭐⭐⭐⭐ Quick |
| **Flexibility** | ⭐⭐⭐⭐⭐ Can specify AVD, wait | ⭐⭐⭐ Count only |
| **Visibility** | ⭐⭐⭐ Text response | ⭐⭐⭐⭐⭐ Visual cards |
| **Automation** | ⭐⭐⭐⭐⭐ Scriptable | ⭐⭐⭐ Manual |

---

## **Recommended Workflow**

### **For Quick Testing:**
```
1. AI Chat: "Launch 3 emulators"
2. Wait 10-15 seconds for boot
3. Multi-Device Page: Select devices and run simulation
```

### **For Visual Monitoring:**
```
1. Navigate to /emulators
2. Click "Launch Emulator" → Enter count
3. Watch emulators boot in real-time
4. Click device cards to start streaming
```

### **For Automated Workflows:**
```
1. AI Chat: "Launch 5 emulators and run login test on all of them"
2. Agent handles everything automatically
```

---

## **Troubleshooting**

### **Emulators not appearing?**
- Wait 10-15 seconds for boot
- Refresh device list
- Check backend logs for errors

### **Black screen on emulator?**
- Already fixed with `-gpu swiftshader_indirect` and `-no-snapshot`
- If still occurs, try launching with `wipe_data=True`

### **Port conflicts?**
- Backend auto-assigns ports starting from 5554
- Each emulator uses 2 ports (5554+5555, 5556+5557, etc.)

---

## **Summary**

✅ **Option 1 (AI Chat)** - Best for automation and quick launches  
✅ **Option 2 (Emulator Page)** - Best for visual monitoring and manual control

Both options use the same backend API and support launching 1-20 emulators concurrently! 🎉

