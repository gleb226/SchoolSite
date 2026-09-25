/**
 * Diary & Attendance helpers
 */

// Helper to calculate student GPA (середній бал)
function calculateGPA() {
    const badges = document.querySelectorAll('.grade-badge');
    if (!badges.length) return null;
    let sum = 0;
    let count = 0;
    badges.forEach(b => {
        const val = parseFloat(b.innerText);
        if (!isNaN(val)) {
            sum += val;
            count++;
        }
    });
    return count > 0 ? (sum / count).toFixed(2) : null;
}
