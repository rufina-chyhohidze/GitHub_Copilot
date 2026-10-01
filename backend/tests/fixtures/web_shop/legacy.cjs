const adapter = require('./external');
module.exports = (value) => adapter.save(value);
