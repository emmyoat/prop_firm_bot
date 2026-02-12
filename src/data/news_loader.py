import requests
import logging
from datetime import datetime, timedelta
import time
import json

logger = logging.getLogger("PropBot.News")

class NewsLoader:
    def __init__(self):
        # Using a public endpoint that provides ForexFactory data in JSON
        # This is a common workaround for bots without scraping
        self.url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json" 
        self.last_update = 0
        self.cached_news = []
        self.blocked_minutes = set() # Set of "YYYY-MM-DD HH:MM" strings

    def update_news(self):
        """Fetches news and updates blocked times."""
        if time.time() - self.last_update < 3600: # Update once per hour
            return

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            logger.info("Fetching Economic News...")
            response = requests.get(self.url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                self.cached_news = data
                self.last_update = time.time()
                self._process_blocked_times()
                logger.info(f"News updated. Found {len(data)} events. Blocked minutes: {len(self.blocked_minutes)}")
            else:
                logger.error(f"Failed to fetch news. Status: {response.status_code}")
                self.last_update = time.time() - 3300 # Retry in 5 mins (3600 - 3300 = 300s)
                
        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            self.last_update = time.time() - 3300 # Retry in 5 mins

    def _process_blocked_times(self):
        """
        Filters High Impact (Red Folder) USD news.
        Blocks 30 mins before and 30 mins after.
        """
        self.blocked_minutes.clear()
        
        for event in self.cached_news:
            # Structure: {"title":..., "country":"USD", "date":"2025-01-12T14:30:00-04:00", "impact":"High", ...}
            
            # Filter
            if event.get('country') != 'USD': continue
            if event.get('impact') != 'High': continue # Only Red Folders
            
            date_str = event.get('date') # ISO format
            if not date_str: continue

            try:
                # Parse date (Handles timezone offset if present, usually UTC or NY)
                # FF JSON is usually Eastern Time or UTC depending on feed.
                # Assuming the feed provides an Offset-aware string.
                # Simplified parsing:
                # "2025-01-12T14:30:00-04:00"
                event_time = datetime.fromisoformat(date_str)
                
                # Convert to naive UTC or Server Time?
                # Best practice: Convert everything to UTC.
                # Since our bot runs on local computer time which matches MT5 server usually...
                # We need to be careful. Let's assume Computer Time = Real Time.
                # We need to convert Event Time to Local System Time.
                
                current_tz_offset = event_time.utcoffset()
                # Making it naive (Local Time) - The CORRECT way to convert to system time
                local_event_time = event_time.astimezone(None).replace(tzinfo=None)
                
                # Block Window (-30 to +30 mins)
                start_block = local_event_time - timedelta(minutes=30)
                end_block = local_event_time + timedelta(minutes=30)
                
                # Populate set
                curr = start_block
                while curr <= end_block:
                    self.blocked_minutes.add(curr.strftime("%Y-%m-%d %H:%M"))
                    curr += timedelta(minutes=1)
                    
            except ValueError:
                continue

    def is_blocked(self, check_time: datetime = None) -> bool:
        """
        Checks if the given time (or now) is in a blocked window.
        """
        if not check_time:
            check_time = datetime.now()
        
        # Ensure we have fresh data
        self.update_news()
        
        # Check specific minute
        time_str = check_time.strftime("%Y-%m-%d %H:%M")
        return time_str in self.blocked_minutes
