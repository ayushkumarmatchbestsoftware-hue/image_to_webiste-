# 🛠️ Product Requirements Document (PRD): SiteForge AI

**Version**: 1.2  
**Status**: Ready for UI/UX & Frontend Development (Unified Workflow Edition)  
**Project Objective**: To build an elite, AI-driven website generator that produces professional, niche-perfect, and "non-AI looking" websites in under 30 seconds.

---

## 1. Product Overview
Pomeli is an AI website builder utilizing **Gemini 3.1 Flash Image Preview** to create fully-themed, multi-page websites. The focus is on **high-end aesthetics**, **Roboto-driven typography**, and **WCAG-compliant accessibility**.

---

## 2. Core Functional Requirements (Asset Handling)

### 2.1 User Input & Assets
*   **Prompt Entry**: A textarea for descriptions (Limit: **1,000 words**).
*   **Optional Files**: 
    *   **5 Images** (Max **5 MB** each)
    *   **1 Logo** (Max **2 MB**)
    *   **30 MB Total** request limit.
*   **Supported Formats**: JPG, PNG, GIF, WEBP, and iPhone-native **HEIC/HEIF**.

---

## 3. The Unified User Journey (UI/UX Flow)

The system uses a **Two-Tab Architecture** (Dashboard ↔ Editor).

### **Phase 1: The Initial Dashboard (Tab 1)**
1.  User enters prompt/uploads assets and clicks **"Create Site."**
2.  Once generated, the UI shows a single primary action: **`🚀 Preview & Download`**.

### **Phase 2: The Visual Editor (Tab 2)**
1.  **Opening Editor**: Clicking the dashboard button opens the site in a **New Tab** (`GET /preview/.../home.html`).
2.  **Live Editing**: All text and images are "editable" (e.g., using `contenteditable` or specialized overlays).
3.  **The Final Action**: At the bottom of the page, the user clicks a single button: **`✅ Save & Download .HTML`**. 
4.  **Backend Chain**:
    *   **Save**: Frontend sends `POST /save-and-build` (syncs edits to server).
    *   **Download**: Frontend immediately triggers `GET /download/<id>` (starts browser file download).
    *   **Completion**: Tab 2 **automatically closes**, returning the user to Tab 1.

### **Phase 3: The Deployment (Back to Tab 1)**
1.  **Site Ready**: The Dashboard (Tab 1) detects that the project is "Finalized."
2.  **Go Live**: The UI now displays a high-impact button: **`🚀 Deploy to Vercel`**.
3.  **Result**: Clicking this triggers the deployment logic, resulting in a **Permanent Live URL**.

---

## 4. API Endpoints & Frontend Responses

### **4.1 Website Generation**
*   **Endpoint**: `POST /generate`
*   **Response**:
    ```json
    {
        "success": true,
        "website_id": "415d1830-4e3e-4613-810a-dd70356c9d0f",
        "preview_url": "/preview/415d1830-4e3e-4613-810a-dd70356c9d0f/home.html"
    }
    ```

### **4.2 Editor Synchronization**
*   **Endpoint**: `POST /save-and-build`
*   **Response**:
    ```json
    {
        "success": true,
        "preview_url": "/preview/<id>/home.html"
    }
    ```

### **4.3 Final Fulfillment**
*   **Endpoint**: `GET /download/<website_id>`
*   **Response**: Binary data (Starts `.html` file download in the browser).

### **4.4 Web Deployment**
*   **Endpoint**: `POST /deploy/vercel`
*   **Response**:
    ```json
    {
        "success": true,
        "live_url": "https://my-boutique-bakery.vercel.app",
        "message": "Deployment Successful!"
    }
    ```

---

## 5. System Normalization (Reliability)
*   **Contrast Guard**: Auto-fixes colors to ensure a **4.5:1 contrast ratio**.
*   **Typography Guard**: Forces fallback to **Roboto** for all fonts.
*   **No-Image Logic**: Automatically uses a `bold-center` layout if no user photos are provided.



Contrast Guard ensures all text is readable by automatically adjusting colors to meet accessibility standards (4.5:1 ratio), so generated websites don’t look visually weak or hard to read.