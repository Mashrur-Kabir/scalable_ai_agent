from locust import HttpUser, task, between
import random
import string

def generate_text(length=500):
    """Generate dummy research paper text of a given length."""
    return ''.join(random.choices(string.ascii_letters + ' ', k=length))

class FastAPIAgentUser(HttpUser):
    # Wait time between tasks to simulate real users
    wait_time = between(1, 3)  # seconds

    @task(3)
    def analyze_paper(self):
        """Simulate sending research paper text for analysis."""
        text = generate_text(800)  # ~800-character payload
        self.client.post(
            "/analyze",
            json={"text": text},
            headers={"Content-Type": "application/json"},
            name="Analyze Research Paper"
        )

