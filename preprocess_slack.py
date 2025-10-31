import json
import os

# --- Configuration ---
HISTORY_FILE = '******_history.json'
PEOPLE_FILE = 'people.json'
OUTPUT_FILE = 'slack_history_optimized.txt'
# ---------------------

def create_people_map(people_filename):
    """
    Loads the people.json file and creates a
    dictionary mapping Slack IDs to display names.
    """
    if not os.path.exists(people_filename):
        print(f"Warning: '{people_filename}' not found. User names will be Slack IDs.")
        return {}
        
    try:
        with open(people_filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Check if the file has a root 'people' key
        if 'people' in data and isinstance(data['people'], list):
            people_list = data['people']
        # Handle cases where the file might just be a list
        elif isinstance(data, list):
            people_list = data
        else:
            print(f"Error: Could not find a 'people' list in '{people_filename}'.")
            return {}

        return {person.get('slackId'): person.get('displayName', 'Unknown') 
                for person in people_list if person.get('slackId')}
                
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{people_filename}'.")
        return {}
    except Exception as e:
        print(f"An error occurred while reading '{people_filename}': {e}")
        return {}

def get_display_name(message, people_map):
    """
    Gets the friendly display name for a message.
    Falls back to 'username' (for bots) or the raw user ID.
    """
    user_id = message.get('user')
    
    if user_id in people_map:
        return people_map[user_id]
    
    # Fallback for bots or users not in people.json
    if 'username' in message:
        return message['username']
        
    if user_id:
        return user_id # Fallback to the ID
        
    return "Unknown User"

def process_slack_history(history_filename, people_map, output_filename):
    """
    Reads the Slack history, groups messages by thread,
    and writes a simplified, human-readable text file.
    """
    if not os.path.exists(history_filename):
        print(f"Error: History file '{history_filename}' not found.")
        return

    print(f"Loading history from '{history_filename}'...")
    try:
        with open(history_filename, 'r', encoding='utf-8') as f:
            history_data = json.load(f)
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{history_filename}'.")
        return
    except Exception as e:
        print(f"An error occurred while reading '{history_filename}': {e}")
        return

    if not isinstance(history_data, list):
        print("Error: History file does not contain a list of messages.")
        return

    print("Grouping messages by thread...")
    threads = {}
    message_count = 0

    for message in history_data:
        # Skip messages without text (e.g., channel joins)
        if message.get('text') is None:
            continue
            
        message_count += 1
        
        # 'thread_ts' exists for replies.
        # If it doesn't exist, it's a parent message,
        # so we use its own 'ts' as the thread key.
        thread_key = message.get('thread_ts', message.get('ts'))
        
        if not thread_key:
            print(f"Skipping message with no 'ts': {message}")
            continue

        if thread_key not in threads:
            threads[thread_key] = []
            
        # Get the essential info
        ts = message.get('ts')
        name = get_display_name(message, people_map)
        text = message.get('text', '').replace('\n', '\n    ') # Indent newlines
        
        threads[thread_key].append((ts, name, text))

    print(f"Processed {message_count} messages into {len(threads)} threads.")
    
    # Sort threads by their starting timestamp
    sorted_thread_keys = sorted(threads.keys())

    print(f"Writing optimized output to '{output_filename}'...")
    with open(output_filename, 'w', encoding='utf-8') as f:
        for thread_key in sorted_thread_keys:
            messages_in_thread = threads[thread_key]
            
            # Sort messages *within* the thread by their individual timestamp
            messages_in_thread.sort(key=lambda m: m[0])
            
            # Write the parent message
            parent_ts, parent_name, parent_text = messages_in_thread[0]
            f.write(f"[{parent_ts}] {parent_name}: {parent_text}\n")
            
            # Write all replies, indented
            for (reply_ts, reply_name, reply_text) in messages_in_thread[1:]:
                f.write(f"    > [{reply_ts}] {reply_name}: {reply_text}\n")
            
            # Add a blank line between threads for readability
            f.write("\n")

    print("Done!")

# --- Main execution ---
if __name__ == "__main__":
    people_mapping = create_people_map(PEOPLE_FILE)
    process_slack_history(HISTORY_FILE, people_mapping, OUTPUT_FILE)