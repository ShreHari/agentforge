from typing import TypedDict


class State(TypedDict):
    """A state in the graph."""

    job_ad: str
    company: str
    notes: list[str]


def validate(state: State) -> dict:
    first_word = state["job_ad"].split()[0]
    return {"company": first_word}


if __name__ == "__main__":
    test_state = {"job_ad": "Acme Ltd is hiring an AI engineer",
                  "company": "", "notes": []}
    print(validate(test_state))  # Output: {'company': 'Acme'}
