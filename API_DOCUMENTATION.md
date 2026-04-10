# Pomeli Website Generator - API Documentation 🚀

This document outlines the end-to-end API integration flow for the Frontend Team. The backend handles AI generation, R2 Media storage, PostgreSQL (Credits), and MongoDB (Website persistence).

**Base URL**: `http://localhost:5077` (Local) / `https://api.yourdomain.com` (Production)
**Authentication**: All endpoints require a Bearer token in the `Authorization` header.

---

## 1. Authentication & Headers

Every request must include the JWT token.
```http
Authorization: Bearer <your_jwt_token>
```
*Note: The backend validates this token securely and checks user balances via the credit service.*

---

## 2. Core Endpoints Flow

### Step 1: Generate Website
Initiates the AI generation process in the background. Deducts zero upfront, credits are charged only when generation finishes successfully.

- **Endpoint**: `POST /generate`
- **Content-Type**: `multipart/form-data`
- **Payload**:
  - `prompt` (string) - Required. The description of the business.
  - `logo` (file) - Optional. The primary brand logo image.
  - `images` (array of files) - Optional (Max 5). Content images.
  - `pages` (string) - Optional. Comma separated (e.g. `hero,about,services`).
  - `palette` (string) - Optional. Predefined enum or `auto`.
  - `industry` (string) - Optional. The business industry.

**Response (200 OK)**:
```json
{
  "success": true,
  "job_id": "8b08e5e7-...",
  "website_id": "4da9bffa-...",
  "status": "queued"
}
```

---

### Step 2: Poll Job Status
Since generation takes 30-60 seconds, poll this endpoint every 3-5 seconds to check if the AI worker is done.

- **Endpoint**: `GET /job-status/<job_id>`
- **Response (Generating)**:
```json
{
  "status": "queued",
  "website_id": "4da9bffa-..."
}
```
- **Response (Completed)**:
```json
{
  "status": "completed",
  "website_id": "4da9bffa-...",
  "url": "/preview/4da9bffa-.../home.html"
}
```

---

### Step 3: Preview Iframe
Once generation is `completed`, mount this URL inside an `<iframe>` in your frontend editor. It securely streams the generated HTML out of your Cloudflare R2 bucket.

- **Endpoint**: `GET /preview/<website_id>/<file_name>`
- **Usage Context**:
```html
<iframe src="https://api.yourdomain.com/preview/4da9bffa-.../home.html"></iframe>
```

---

### Step 4: AI Chat Editor (Iterative Updates)
Allows the user to converse with the AI to tweak the currently viewed page. This specifically **deducts 1 credit per successful edit**.

- **Endpoint**: `POST /chat-edit`
- **Content-Type**: `application/json`
- **Payload**:
```json
{
  "website_id": "4da9bffa-...",
  "current_html": "<html>... (the raw html of the iframe) ...</html>",
  "instruction": "Make the background blue instead of red"
}
```
**Response (200 OK)**:
```json
{
  "success": true,
  "html": "<html>... (the new updated html) ...</html>",
  "summary": "Website updated successfully"
}
```
*(The frontend team should then take this `html` and inject it immediately into the Iframe for real-time update)*

---

### Step 5: Save & Reorder Pages
If the user uses a Drag-and-Drop builder to reorder sections, post the new layout array to this endpoint. This syncs the saved state in MongoDB.

- **Endpoint**: `POST /save/<website_id>`
- **Content-Type**: `application/json`
- **Payload**:
```json
{
  "layout": ["hero", "services", "about", "contact"]
}
```
**Response (200 OK)**: `{"success": true}`

---

### Step 6: Deploy to Production
Triggers a live deployment of the generated files to a Vercel instance.

- **Endpoint**: `POST /deploy/<website_id>`
- **Content-Type**: `application/json`
- **Payload**: `{}`
**Response (200 OK)**:
```json
{
  "success": true,
  "url": "https://pomeli-4da9bffa-1234.vercel.app"
}
```

---

### Step 7: Download Source Code
Downloads all HTML strings, zips them together, and streams the `.zip` to the browser.

- **Endpoint**: `GET /download/<website_id>`
- **Response**: Standard HTTP binary stream trigger for `website_<id>.zip`.

---

## Error Handling
If the user does not have enough credits or provides invalid auth, expect standard HTTP blocks:
- `401 Unauthorized` - Token missing or expired.
- `402 Payment Required` - The user has 0 credits.
- `400 Bad Request` - Missing variables.
- `500 Server Error` - Worker failed or AI crashed.
