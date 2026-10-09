// Thin wrappers around the CGI JSON endpoints.

export function fetchData(type, callback) {
    fetch('?action=api&type=' + type)
        .then(response => response.json())
        .then(data => callback(data))
        .catch(error => console.error('Error fetching ' + type, error));
}

export function postJson(action, payload) {
    return fetch('?action=' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).then(response => response.json());
}
