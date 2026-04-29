import time
import requests
import concurrent.futures
import os

# Configuration
INPUT_FILE = 'streams.txt'
TIMEOUT_SECONDS = 60 
MAX_CONCURRENT_WORKERS = 60 # Adjust based on how many streams you are testing

def measure_ttfb(url):
    """
    Connects to a stream and measures the time taken to receive the first byte.
    """
    url = url.strip()
    if not url:
        return None

    # Ensure the URL has a scheme
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    start_time = time.time()
    
    try:
        # stream=True connects and downloads headers, but pauses before downloading the body
        with requests.get(url, stream=True, timeout=TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            
            # Fetch the very first byte of the actual stream content
            for chunk in response.iter_content(chunk_size=1):
                if chunk:
                    break
                    
        ttfb = (time.time() - start_time) * 1000 # Convert to milliseconds
        return {'url': url, 'ttfb': ttfb, 'error': None}
        
    except requests.exceptions.Timeout:
        return {'url': url, 'ttfb': None, 'error': 'Connection timed out'}
    except Exception as e:
        return {'url': url, 'ttfb': None, 'error': str(e)}

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found in the current directory.")
        return

    # Read the URLs from the text file
    with open(INPUT_FILE, 'r') as file:
        urls = [line.strip() for line in file.readlines() if line.strip()]

    if not urls:
        print(f"No URLs found in {INPUT_FILE}.")
        return

    print(f"Starting TTFB measurement for {len(urls)} streams...\n")
    print("-" * 65)
    print(f"{'URL':<45} | {'TTFB (ms)':<15}")
    print("-" * 65)

    # Use ThreadPoolExecutor to request multiple streams concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        # Map the function to the URLs
        results = executor.map(measure_ttfb, urls)
        
        # Process and print results as they complete
        for result in results:
            if result is None:
                continue
                
            if result['error']:
                print(f"{result['url'][:43]:<45} | ERROR: {result['error']}")
            else:
                print(f"{result['url'][:43]:<45} | {result['ttfb']:.2f} ms")

    print("-" * 65)
    print("\nMeasurement complete.")

if __name__ == "__main__":
    main()
