#!/usr/bin/env python3
import os
import sys

def check_cookie_file(file_path):
    """Check if a cookie file exists and contains YouTube authentication cookies"""
    if not os.path.exists(file_path):
        print(f"ERROR: Cookie file not found at: {file_path}")
        return False
    
    file_size = os.path.getsize(file_path)
    print(f"Cookie file found: {file_path}")
    print(f"File size: {file_size} bytes")
    
    # Open and check content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"ERROR reading file: {e}")
        try:
            # Try again with different encoding
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as e:
            print(f"Failed to read file: {e}")
            return False
    
    # Check for YouTube authentication cookies
    auth_cookies = ["SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID"]
    found = 0
    
    for cookie in auth_cookies:
        if cookie in content:
            found += 1
            print(f"Found authentication cookie: {cookie}")
    
    if found > 0:
        print(f"\nFound {found} authentication cookies.")
        print("This should be sufficient for playing age-restricted content.")
        return True
    else:
        print("\nWARNING: No YouTube authentication cookies found!")
        print("This cookie file may not work for age-restricted content.")
        print("\nRecommendations:")
        print("1. Make sure you are logged into YouTube in your browser before exporting cookies")
        print("2. Use a tool like 'Get cookies.txt' extension for Chrome/Firefox to export cookies")
        print("3. Ensure your YouTube account has age verification completed")
        return False

if __name__ == "__main__":
    # Get the script directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cookie_file = os.path.join(script_dir, "cookies.txt")
    
    check_cookie_file(cookie_file) 