import re
import json
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, List
import ollama
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Intent(Enum):
    TASK_MANAGEMENT = "task_management"
    TASK_QUERY = "task_query"
    PRODUCTIVITY = "productivity"
    LOCATION_QUERY = "location_query"
    GENERAL_QUERY = "general_query"
    UNKNOWN = "unknown"

def classify_intent(user_input: str) -> Intent:
    prompt = f"""
    Classify the following user input into one of these intents:
    - TASK_MANAGEMENT: for adding, modifying, or completing tasks
    - TASK_QUERY: for checking tasks or suggesting time slots
    - PRODUCTIVITY: for questions or requests about being more productive
    - LOCATION_QUERY: for finding places or getting location-based information
    - GENERAL_QUERY: for general questions or requests not fitting the above categories
    - UNKNOWN: if the intent is unclear

    User input: "{user_input}"

    Respond with the intent label and a brief explanation of why you chose it.
    """
    response = ollama.generate(model="adrienbrault/nous-hermes2pro-llama3-8b:q8_0", prompt=prompt)
    response_text = response['response'].strip().lower()
    
    for intent in Intent:
        if intent.value in response_text:
            return intent
    
    return Intent.UNKNOWN

def extract_date_time(text: str) -> datetime:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.now()

def extract_structured_data(user_input: str, intent: Intent) -> Dict[str, Any]:
    data = {}
    if intent == Intent.TASK_MANAGEMENT:
        task_match = re.search(r'(?:add|modify|complete) (?:task|todo) (.*?)(?:on|at|for|$)', user_input, re.IGNORECASE)
        data['task_name'] = task_match.group(1).strip() if task_match else input("Task name: ")
        date_time_match = re.search(r'(?:on|at|for) (.*?)(?:$|\s)', user_input, re.IGNORECASE)
        data['due'] = extract_date_time(date_time_match.group(1)) if date_time_match else extract_date_time(input("Due date? (YYYY-MM-DD HH:MM): "))
    elif intent == Intent.TASK_QUERY:
        date_range_match = re.search(r'from (.*?) to (.*?)(?:$|\s)', user_input, re.IGNORECASE)
        if date_range_match:
            data['start_date'] = datetime.strptime(date_range_match.group(1), "%Y-%m-%d").date()
            data['end_date'] = datetime.strptime(date_range_match.group(2), "%Y-%m-%d").date()
        else:
            data['start_date'] = datetime.now().date()
            data['end_date'] = data['start_date'] + timedelta(days=1)
    elif intent in [Intent.PRODUCTIVITY, Intent.LOCATION_QUERY, Intent.GENERAL_QUERY, Intent.UNKNOWN]:
        data['query'] = user_input
    return data

def correct_time_format(time_description: str) -> str:
    prompt = f"""
    The following time description is not in a standard format. Convert it into a format like 'HH:MM AM/PM':
    "{time_description}"
    """
    response = ollama.generate(model="adrienbrault/nous-hermes2pro-llama3-8b:q8_0", prompt=prompt)
    corrected_time = response['response'].strip()
    return corrected_time

class TaskWarriorIntegration:
    def get_tasks(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        command = f"task due.after:{start_date.strftime('%Y-%m-%d')} due.before:{end_date.strftime('%Y-%m-%d')} export"
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        tasks = []
        try:
            tasks = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from TaskWarrior: {e}")
        return tasks

    def add_task(self, task: Dict[str, Any]):
        command = f"task add \"{task['summary']}\" due:{task['due'].strftime('%Y-%m-%dT%H:%M:%S')}"
        logger.info(f"Executing command: {command}")
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            logger.info(f"Task added successfully: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error adding task: {e.stderr}")
            raise RuntimeError(f"Failed to add task: {e.stderr}")

    def complete_task(self, task_id: str) -> bool:
        command = f"task {task_id} done"
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return "Completed task" in result.stdout

    def get_completed_tasks(self) -> List[Dict[str, Any]]:
        command = "task status:completed export"
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        tasks = []
        try:
            tasks = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from TaskWarrior: {e}")
        return tasks

class ActionExecutor:
    def __init__(self, taskwarrior: TaskWarriorIntegration):
        self.taskwarrior = taskwarrior

    def add_task(self, data: Dict[str, Any]) -> str:
        if not all([data.get('task_name'), data.get('due')]):
            raise ValueError("Missing required data for adding task")
        task = {
            "summary": data['task_name'],
            "due": data['due']
        }
        self.taskwarrior.add_task(task)
        return f"Added task: {data['task_name']} due on {data['due']}"

    def check_tasks(self, data: Dict[str, Any]) -> str:
        tasks = self.taskwarrior.get_tasks(data['start_date'], data['end_date'])
        tasks_list = "\n".join([json.dumps(task) for task in tasks])
        return f"Your tasks from {data['start_date']} to {data['end_date']}:\n" + tasks_list

    def complete_task(self, data: Dict[str, Any]) -> str:
        if not data.get('task_name'):
            raise ValueError("Missing task name for completing task")
        if self.taskwarrior.complete_task(data['task_name']):
            return f"Completed task: {data['task_name']}"
        return f"Failed to complete task: {data['task_name']}. Task not found."

    def handle_productivity(self, data: Dict[str, Any]) -> str:
        prompt = f"""
        The user has asked for help with productivity: "{data.get('query', '')}"
        Provide a helpful response with 2-3 practical tips for improving productivity.
        For each tip, suggest a specific action the user can take today, including a suggested time and duration.
        Format each action as: ACTION: [action description] | TIME: [HH:MM AM/PM] | DURATION: [minutes]
        """
        response = ollama.generate(model="adrienbrault/nous-hermes2pro-llama3-8b:q8_0", prompt=prompt)
        advice = response['response'].strip()
        
        logger.info(f"Generated productivity advice: {advice}")
        
        # Extract actions from the advice
        actions = re.findall(r'ACTION: (.*?) \| TIME: (.*?) \| DURATION: (.*?)(?:\n|$)', advice, re.DOTALL)
        
        logger.info(f"Extracted actions: {actions}")
        
        # Schedule actions as tasks
        scheduled_tasks = []
        for action, time, duration in actions:
            while True:
                try:
                    # Attempt to parse the time
                    due_time = datetime.combine(datetime.now().date(), datetime.strptime(time.strip(), "%I:%M %p").time())
                    break
                except ValueError:
                    # Correct the time format if parsing fails
                    time = correct_time_format(time.strip())
                
            try:
                task = {
                    "summary": action.strip(),
                    "due": due_time
                }
                logger.info(f"Attempting to add task: {task}")
                self.taskwarrior.add_task(task)
                scheduled_tasks.append(f"Added task: {action.strip()} due at {due_time.strftime('%I:%M %p')}")
                logger.info(f"Successfully added task: {action.strip()}")
            except Exception as e:
                logger.error(f"Error scheduling task: {e}", exc_info=True)
                scheduled_tasks.append(f"Failed to add task: {action.strip()} due to an error")
        
        return advice + "\n\n" + "\n".join(scheduled_tasks)

    def handle_location_query(self, data: Dict[str, Any]) -> str:
        prompt = f"""
        The user is looking for: "{data.get('query', '')}"
        Provide a helpful response as if you're suggesting 2-3 places or options related to their query.
        """
        response = ollama.generate(model="adrienbrault/nous-hermes2pro-llama3-8b:q8_0", prompt=prompt)
        return response['response'].strip()

    def handle_general_query(self, data: Dict[str, Any]) -> str:
        prompt = f"""
        The user has asked: "{data.get('query', '')}"
        Provide a helpful and informative response to this query.
        """
        response = ollama.generate(model="adrienbrault/nous-hermes2pro-llama3-8b:q8_0", prompt=prompt)
        return response['response'].strip()

action_map = {
    Intent.TASK_MANAGEMENT: ActionExecutor.add_task,
    Intent.TASK_QUERY: ActionExecutor.check_tasks,
    Intent.PRODUCTIVITY: ActionExecutor.handle_productivity,
    Intent.LOCATION_QUERY: ActionExecutor.handle_location_query,
    Intent.GENERAL_QUERY: ActionExecutor.handle_general_query,
    Intent.UNKNOWN: ActionExecutor.handle_general_query,
}

def generate_api_call(intent: Intent, data: Dict[str, Any], executor: ActionExecutor) -> Callable:
    action_function = action_map.get(intent)
    if action_function is None:
        raise ValueError(f"No action mapped for intent: {intent}")
    return lambda: action_function(executor, data)

class AISecretary:
    def __init__(self, config: Dict[str, Any]):
        self.taskwarrior = TaskWarriorIntegration()
        self.action_executor = ActionExecutor(self.taskwarrior)

    def process_request(self, user_input: str) -> str:
        intent = classify_intent(user_input)
        try:
            structured_data = extract_structured_data(user_input, intent)
            if intent == Intent.UNKNOWN:
                intent = Intent.GENERAL_QUERY
            api_call = generate_api_call(intent, structured_data, self.action_executor)
            result = api_call()
            
            response = f"{result}"
            if intent == Intent.PRODUCTIVITY:
                response += "\n\nI've added these suggestions as tasks. You can modify or remove them as needed."
            elif intent not in [Intent.GENERAL_QUERY, Intent.LOCATION_QUERY]:
                insights = self.get_insights_from_completed_tasks()
                if insights:
                    response += f"\n\nBased on your completed tasks, here's an insight: {insights}"
            
            return response
        except ValueError as e:
            return f"I'm sorry, but I couldn't process your request: {str(e)}. Could you please provide more details?"
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            return f"I encountered an unexpected error: {str(e)}. Please try again or contact support if the issue persists."

    def get_insights_from_completed_tasks(self) -> str:
        completed_tasks = self.taskwarrior.get_completed_tasks()
        if not completed_tasks:
            return ""

        completed_tasks_str = "\n".join([json.dumps(task) for task in completed_tasks])
        prompt = f"""
        Based on the following completed tasks, provide a brief insight or suggestion for the user:

        {completed_tasks_str}

        Insight:
        """
        response = ollama.generate(model="adrienbrault/nous-hermes2pro-llama3-8b:q8_0", prompt=prompt)
        return response['response'].strip()

def test_taskwarrior():
    print("Testing TaskWarrior functionality...")
    taskwarrior = TaskWarriorIntegration()
    test_task = {
        "summary": "Test task from Python",
        "due": datetime.now() + timedelta(days=1)
    }
    try:
        taskwarrior.add_task(test_task)
        print("Test task added successfully.")
        tasks = taskwarrior.get_tasks(datetime.now(), datetime.now() + timedelta(days=2))
        tasks_list = "\n".join([json.dumps(task) for task in tasks])
        print(f"Current tasks: {tasks_list}")
    except Exception as e:
        print(f"Error during TaskWarrior test: {e}")

# Example usage
if __name__ == "__main__":
    config = {}  # Add any necessary configuration here
    assistant = AISecretary(config)
    print("Welcome to your AI Secretary! How can I help you today?")
    print("(Type 'exit' to quit)")
    while True:
        user_input = input("\nYou: ")
        if user_input.lower() == 'exit':
            print("Thank you for using AI Secretary. Goodbye!")
            break
        response = assistant.process_request(user_input)
        print(f"\nAssistant: {response}")
