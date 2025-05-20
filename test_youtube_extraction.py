#!/usr/bin/env python3
import yt_dlp
import asyncio
import sys
import os

async def test_extraction_methods(video_id="8jZLYF7WNKs"):
    """Test different methods of extracting YouTube content"""
    print(f"Testing extraction methods for video ID: {video_id}")
    
    methods = [
        {
            "name": "Standard YouTube",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "opts": {
                "quiet": True,
                "no_warnings": False,
                "age_limit": 99,
                "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None
            }
        },
        {
            "name": "YouTube Music",
            "url": f"https://music.youtube.com/watch?v={video_id}",
            "opts": {
                "quiet": True,
                "no_warnings": False,
                "cookiefile": "cookies.txt" if os.path.exists("cookies.txt") else None
            }
        },
        {
            "name": "YouTube Mobile",
            "url": f"https://m.youtube.com/watch?v={video_id}",
            "opts": {
                "quiet": True,
                "no_warnings": False,
                "extractor_args": {"youtube": {"player_client": ["android"]}}
            }
        },
        {
            "name": "YouTube with innertube",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "opts": {
                "quiet": True,
                "no_warnings": False,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web"],
                        "player_skip": ["webpage", "configs"],
                    }
                }
            }
        },
        {
            "name": "YouTube Embed",
            "url": f"https://www.youtube.com/embed/{video_id}",
            "opts": {"quiet": True, "no_warnings": False}
        },
        {
            "name": "YouTube Short URL",
            "url": f"https://youtu.be/{video_id}",
            "opts": {"quiet": True, "no_warnings": False}
        }
    ]
    
    for method in methods:
        print(f"\nTrying method: {method['name']}")
        print(f"URL: {method['url']}")
        
        try:
            with yt_dlp.YoutubeDL(method["opts"]) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(method["url"], download=False)
                )
                
                if info:
                    print(f"✅ SUCCESS: Title: {info.get('title', 'Unknown')}")
                    print(f"Duration: {info.get('duration', 'Unknown')}")
                    
                    formats = info.get('formats', [])
                    audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                    
                    if audio_formats:
                        print(f"Found {len(audio_formats)} audio formats")
                        best_audio = max(audio_formats, key=lambda f: f.get('abr', 0) or 0)
                        print(f"Best audio: {best_audio.get('format_id')} - {best_audio.get('ext')} - {best_audio.get('abr')}kbps")
                    else:
                        print("No audio-only formats found")
                else:
                    print("❌ FAILED: Could not extract info")
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")

if __name__ == "__main__":
    video_id = sys.argv[1] if len(sys.argv) > 1 else "8jZLYF7WNKs"
    asyncio.run(test_extraction_methods(video_id)) 