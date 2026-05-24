import time
import random

mock_database = {
    '60min': {
        'last_fetch': 0,
        'data': None
    },
    '24hr': {
        'last_fetch': 0,
        'data': None
    },
    '7day': {
        'last_fetch': 0,
        'data': None
    }
}


def generate_mock_data(time_period: str, base_timestamp: float):
    """Generate mock data for different time periods"""
    current_time = time.time()
    timestamp = current_time # 5 minutes in seconds
    data ={
        'nodes': [
            {
                'id': 0,
                'type': 'gen',
                'status': 1,
                'output': 450 + random.randint(-50, 50),
                'targets': [1, 2]
            },
            {
                'id': 1,
                'type': 'battery',
                'status': 1,
                'output': 180 + random.randint(-30, 30),
                'charge_level': 70 + random.randint(-10, 15),
                'targets': [2]
            },
            {
                'id': 2,
                'type': 'load',
                'status': 1,
                'output': 90 + random.randint(-20, 20),
                'priority': 1,
                'targets': []
            },
            {
                'id': 3,
                'type': 'load',
                'status': 1,
                'output': 380 + random.randint(-40, 40),
                'priority': 10,
                'targets': []
            },
            {
                'id': 4,
                'type': 'grid',
                'status': 1,
                'output': 380 + random.randint(-50, 50),
                'targets': [3]
            },
        ],
        'weather': {
            'rain': random.randint(0, 5),
            'sunlight': 85 + random.randint(-15, 15),
            'clouds': random.randint(0, 60)
        },
        'grid': {
            'status': 1,
            'output': 480 + random.randint(-50, 50),
            'carbon_intensity': 280 + random.randint(-30, 30)
        }
    }
    return data