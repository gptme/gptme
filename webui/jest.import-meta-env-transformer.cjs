const ts = require('typescript');

function isImportMetaEnv(node) {
  return (
    ts.isPropertyAccessExpression(node) &&
    ts.isPropertyAccessExpression(node.expression) &&
    ts.isMetaProperty(node.expression.expression) &&
    node.expression.name.text === 'env'
  );
}

function factory(program) {
  return (context) => {
    const { factory: nodeFactory } = context;

    function visitor(node) {
      if (ts.isPropertyAccessExpression(node) && isImportMetaEnv(node)) {
        return nodeFactory.createElementAccessExpression(
          nodeFactory.createPropertyAccessExpression(
            nodeFactory.createIdentifier('process'),
            'env'
          ),
          nodeFactory.createStringLiteral(node.name.text)
        );
      }
      return ts.visitEachChild(node, visitor, context);
    }

    return (sourceFile) => ts.visitNode(sourceFile, visitor);
  };
}

module.exports = { factory, name: 'import-meta-env', version: 1 };
