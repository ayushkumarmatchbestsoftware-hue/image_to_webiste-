# 🎨 UI/UX Design Requirements Document: SiteForge AI (Pomeli)

**Version**: 2.1  
**Target Audience**: UI/UX Designers & Product Managers
**Project Objective**: To design an elite, frictionless user journey that allows non-technical users to generate, visually edit, and officially deploy premium, "non-AI-looking" websites in under a minute.

---


## 1. The End-to-End User Journey (Macro Flow)

The Pomeli Web Builder utilizes a strict **Two-Tab Architecture**. There are exactly two distinct UI layouts:

1. **The Generation Dashboard (Tab 1)**: The primary data entry form.
2. **The Live Editor & Deployment Canvas (Tab 2)**: An interactive workspace that opens in a completely separate browser tab after generation starts.

---

### **2.1 Sequence 1: Core Configuration**
The UI designers must implement form components in the following chronological user-input order, mirroring the official mockup provided:

1.  **Business Name** `[REQUIRED | Text Input]`
    *   **UI Type**: Standard single-line text input.
    *   **Placeholder Example**: "Apex Studio"
    *   **Function**: Primary brand name displayed across the website header and title.

2.  **What does your business do? (Prompt)** `[REQUIRED | Text Input]`
    *   **UI Type**: Large responsive textarea.
    *   **Function**: Provides the raw context for the AI to generate tailored content.
    *   **Constraint (Limit)**: Absolute maximum of 1,000 words. Real-time word counter required.

3.  **Industry / Type** ` [Default: 'Let AI decide (auto-detect)']`
    *   **UI Type**: Fixed Dropdown List.
    *   **Function**: Informs the design logic. 'Auto-detect' uses the business description to choose the best-fit niche.
    *   **The 11 Official UI Options**:
        1.  `Let AI decide (auto-detect)`
        2.  `Restaurant / Food`
        3.  `SaaS / Tech`
        4.  `Agency / Studio`
        5.  `Law Firm / Legal`
        6.  `Consulting / Professional`
        7.  `Spa / Wellness`
        8.  `Gym / Fitness`
        9.  `Personal Portfolio`
        10. `E-commerce / Retail`
        11. `Real Estate / Property`

4.  **Website Type (Pages)** `[Default: Home]`
    *   **UI Type**: Multi-select pills or checkboxes.
    *   **Function**: Determines which sub-pages(sections) are generated.

5.  **Visual Style (Personality)** `[REQUIRED UI Component | Default: 'Let AI choose']`
    *   **UI Type**: High-impact Visual Cards or Swatches.
    *   **Function**: Selection of design personality is MANDATORY in the UI. If the user skips choosing a specific mood, 'Let AI choose' remains the default logic.
    *   **Personality Profiles**: 
        - `Elegant & Timeless`
        - `Modern & Clean` 
        - `Vibrant & Bold`
        - `Dark & Sophisticated`
        - `Minimalist` (Maximized white space)

### **2.2 Sequence 2: Media Uploads (Drag & Drop Zone)**
The UI team must design a sleek, unified drop-zone or two separate uploaders positioned *after* the core configuration:

5.  **Logo Upload** `[OPTIONAL | Default: None]`
    *   **Limit**: 1 file maximum.
    *   **Size Constraint**: Max 2 MB.

6.  **Images (Product/Vibe Shots)** `[OPTIONAL | Default: Generative AI Images used]`
    *   **Limit**: Maximum 5 images.
    *   **Size Constraint**: Max 5 MB per image.
    *   **Accepted Formats**: `.JPG, .PNG, .GIF, .WEBP, .HEIC, .HEIF`

*   **Total Request Size Limit**: 30 MB absolute max across all files combined.

*   **Primary CTA**: A massive, highly encouraging button: **"✨ Generate My Website"**

---

## 3. The Generation & Tab Handoff
**Goal**: Handle the AI generation queue (takes ~20-40 seconds) without overriding or freezing the dashboard.

*   **The Handoff Mechanism**: 
    1.  When the user clicks "Generate", Tab 1 must NOT transition into a new UI. The form stays exactly as is (perhaps just adding a small, non-intrusive loading spinner on the button itself).
    2.  The browser immediately opens a brand new window / tab (**Tab 2**).
    3.  Inside **Tab 2**, display a skeleton loader or fun cycling text (e.g., "Analyzing Industry...", "Selecting Layouts...") while pinging the backend until the generated website drops in.

---

## 4. Stage 2: The Live Editor & Deployment Canvas (Tab 2)
**Goal**: Once generation is completed, transition the user directly into a full-screen editing workspace.

### **4.1 The Live Website Preview**
*   **Visual Layout**: The generated website sits center-screen.
*   **Interactions for Users**:
    *   **Text Editing**: When users click on text (Headlines, paragraphs), they can edit it natively on the screen like a Word Document. (Show a subtle hover border on editable text).
    *   **Image Replace(The only drag option)**: When users hover over an image, a dark overlay with "Replace Image" appears. Users can drag an image file directly from their desktop and drop it onto the image target to instantly swap it natively. (Note: There is no drag-and-drop section sorting, just image replacing and text typing).

### **4.2 The AI Chat Assistant**
*   **Visual Layout**: A floating chat widget (or toggleable sidebar) where the user can natively talk to the AI to execute bulk edits.
*   **Placeholder Example**: "Make the tone of the whole website more aggressive and mysterious."
*   **Action**: When the user hits send, a local loading spinner appears within the chat while the AI natively updates the website preview in real-time.

### **4.3 The "Final Actions" Floating Control Bar**
*   **Location**: A fixed, floating "pill-shaped" glassmorphism bar placed at the **Bottom-Right** of the screen.
*   **Components**:
    1.  **Save & Download .ZIP**: A highly visible button to save progress and download the offline HTML source files.
    2.  **Deploy to Vercel**: A premium, distinguished CTA (perhaps with a rocket icon). 
        *   **Action**: When clicked, it turns into a brief loading state. Upon success, a beautiful modal or toast notification appears displaying the permanent live URL (e.g., `https://my-site-hash.vercel.app`) with a "Copy Link" and "Visit Site" button.
        *   **(Note)**: There is NO "Undo All" button in this bar. Users undo edits by simply giving the AI Chat Assistant a new instruction to revert changes.

---

## 5. UI Guardrails & Error Handling

*   **File Size Errors**: If a user drops a 10MB image, the UI must intercept it instantly and show an elegant red "File too large (Max 5MB)" error attached to the specific file.
*   **Missing Fields**: If the Business Prompt is empty, clearly highlight the textarea red before allowing the backend request.
*   **Deployment Errors**: If Vercel fails or times out, present a friendly, actionable error dialog with a "Retry" button rather than technical json jargon.

---

## 6. Feature Delivery Map (The User's POV)

To assist with wireframing and prototyping, here is the chronological timeline of features the user will natively experience:

**Step 1. The Entry Point (Dashboard Form)**
* The user interacts with form inputs categorized by required text constraints (1,000 words), optional dropdowns, and file upload validations (Max 30 MB combined).
* **Action Trigger**: The user clicks the main CTA `"Generate My Website"`.

**Step 2. The Core Hand-Off**
* The visual dashboard remains entirely open on the current screen. A loading spinner attaches to the clicked button to acknowledge the click.
* **Action Trigger**: The browser immediately spawns a background request and automatically opens a **brand new browser tab** pointing to the workspace workspace URL.

**Step 3. The Workspace Loading State**
* Inside the newly opened tab, the UI team must design a high-end transitional state (e.g., cycling status text: `"Analyzing your industry..."` -> `"Creating your layout..."`).
* **Action Trigger**: Once the backend queue resolves, the skeleton UI snaps directly into the fully operational, interactive Website Preview.

**Step 4. Live Modifications**
* **Inline Text**: The user directly interacts with the visible text, treating the screen like a native document.
* **Image Drag**: The user drags a `.png` from their desktop and drops it natively over any website photo to instantly swap it.
* **AI Chat**: The user opens the floating AI Chat Module and types `"Make this darker,"` relying on the backend to automatically refresh the live preview.

**Step 5. Final Fulfillment**
* When completely satisfied, the user interacts with the glassmorphic Bottom-Right Control Bar.
* **Offline Path**: Clicking `"Save & Download .ZIP"` streams an offline-ready source code file natively into their browser's download manager.
* **Live Path**: Clicking `"Deploy to Vercel"` triggers a micro-loading state, culminating in a celebration modal presenting their permanent, live `vercel.app` URL.