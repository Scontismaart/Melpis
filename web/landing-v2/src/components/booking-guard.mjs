/** Pure local gate for the illustrative reservation outcome. */
export function bookingAllowed({ capacity, partySize, availabilityVerified, customerConfirmed }) {
  return Number.isInteger(capacity)
    && Number.isInteger(partySize)
    && capacity >= partySize
    && partySize > 0
    && availabilityVerified === true
    && customerConfirmed === true;
}
