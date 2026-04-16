# Olive Tree Lead Machine — Setup Guide

Follow these steps exactly, in order. Takes about 30 minutes total.

---

## Step 1 — Create a GitHub Account

1. Go to **github.com** and click "Sign up"
2. Use any email. Free account is all you need.
3. Download **GitHub Desktop** from **desktop.github.com** and install it
4. Sign in to GitHub Desktop with your new account

---

## Step 2 — Create a Supabase Account (your database)

1. Go to **supabase.com** and click "Start your project" (free)
2. Sign up with GitHub (easiest)
3. Click **New Project**
   - Name it: `olive-tree-leads`
   - Set a database password (save it somewhere — you won't need it often)
   - Region: **US East** (closest to Ontario)
4. Wait ~2 minutes for the project to spin up

**Get your credentials:**
- In your Supabase project, click the **Settings** gear (left sidebar)
- Click **API**
- Copy two things and paste them into a Notes file:
  - **Project URL** (looks like `https://abcdef.supabase.co`)
  - **anon public key** (long string starting with `eyJ`)

**Create the database tables:**
- In Supabase, click **SQL Editor** (left sidebar)
- Click **New Query**
- Open the file `supabase_setup.sql` from this folder
- Copy ALL the text and paste it into the SQL editor
- Click **Run** (green button)
- You should see "Success. No rows returned"

---

## Step 3 — Push the App to GitHub

1. Open **GitHub Desktop**
2. Click **File → New Repository**
   - Name: `olive-tree-leads`
   - Local path: pick a folder on your computer (e.g. Desktop)
   - Click **Create Repository**
3. In GitHub Desktop, click **Show in Finder/Explorer**
4. Copy ALL the files from this `olive-tree-leads` folder into that location
5. Back in GitHub Desktop, you'll see all the files listed
6. In the bottom left, type a summary: `Initial commit`
7. Click **Commit to main**
8. Click **Publish repository** (top right)
   - Keep it **Public** (required for free Streamlit hosting)
   - Click **Publish Repository**

---

## Step 4 — Add Secrets to GitHub (for the daily scheduler)

1. Go to **github.com** and open your `olive-tree-leads` repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** — add these two:
   - Name: `SUPABASE_URL` → Value: your Project URL from Step 2
   - Name: `SUPABASE_KEY` → Value: your anon public key from Step 2

---

## Step 5 — Deploy on Streamlit Cloud

1. Go to **share.streamlit.io** and click "Sign in with GitHub"
2. Click **New app**
3. Select your `olive-tree-leads` repository
4. Main file path: `app.py`
5. Click **Advanced settings** → **Secrets**
6. Paste this (replacing with your real values from Step 2):
   ```
   SUPABASE_URL = "https://YOUR_PROJECT_ID.supabase.co"
   SUPABASE_KEY = "YOUR_ANON_KEY"
   ```
7. Click **Deploy**
8. Wait ~2 minutes. You'll get a public URL like `https://olive-tree-leads.streamlit.app`

---

## Step 6 — Run First-Time Setup (loads all the data)

1. Open your new app URL
2. In the left sidebar, click **⚙️ Admin**
3. Click **▶ Run Full Setup**
4. This will:
   - Load your 356 customers into the database
   - Geocode their addresses (~6 minutes)
   - Fetch residential properties across Grey-Bruce from OpenStreetMap
   - Score every lead based on proximity to your customers
5. When it says "Setup complete!" — you're live

---

## Daily Operation

**The app runs itself.** Every morning at 6am the GitHub Actions scheduler automatically refreshes lead scores.

Your sales guy opens the app URL on his phone:
- **Daily List** — today's top 25 scored leads + one-tap Google Maps route
- **Job Proximity** — type in the job address, get nearby doors to knock
- **Territory Map** — full visual of the service area

After every knock, he taps one of four outcome buttons:
✅ Booked | 🔁 Callback | 🚫 Not Interested | 🏠 Not Home

Visited homes drop off the daily list automatically.

---

## When You Get New Customers from Jobber

1. Export a new customer list from Jobber (same format as before)
2. Replace `data/customers.csv` in your GitHub repo with the new file
3. In the app sidebar, click **🔄 Daily Refresh**

That's it. The new customers become reference points that boost scores of nearby leads.

---

## Total Monthly Cost: $0

- GitHub: Free
- Supabase: Free (well within limits)
- Streamlit Cloud: Free
