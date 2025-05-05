import requests
import os

# Set your NewsAPI key
NEWS_API_KEY = "39f62a7f7b3c4d7fa8520afac79deebd" # Or directly replace it with your API key like: 'your_api_key_here'
NEWS_API_URL = "https://newsapi.org/v2/everything"

def fetch_bitcoin_news():
    # Define query parameters
    params = {
        'q': 'Bitcoin',  # search for Bitcoin-related news
        'apiKey': NEWS_API_KEY,
        'language': 'en',
        'pageSize': 3  # Fetch only the top 3 news articles
    }

    try:
        # Send the GET request
        response = requests.get(NEWS_API_URL, params=params)
        response.raise_for_status()

        # Parse JSON response
        articles = response.json().get("articles", [])
        
        # If there are articles, return the top 3
        if articles:
            news_summary = ""
            for article in articles:
                title = article.get('title')
                description = article.get('description', 'No description available.')
                url = article.get('url')
                news_summary += f"📰 {title}\n{description}\nRead more: {url}\n\n"
            return news_summary
        else:
            return "No recent news found for Bitcoin."
    except Exception as e:
        return f"Error fetching news: {e}"

# Example usage:
news = fetch_bitcoin_news()
print(news)  # Print the fetched news to check
