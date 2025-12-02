import fs from "fs";
import { marked } from "marked";

const [,, inputFile, outputFile] = process.argv;

if (!inputFile || !outputFile) {
  console.error("Usage: node md-to-html.js recipe.md recipe.html");
  process.exit(1);
}
console.log(`Input filename is ${getFilenameWithoutPathOrExtension(inputFile)}`);
const markdownText = fs.readFileSync(inputFile, "utf-8");
const htmlContent = marked(markdownText);

const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>${getFilenameWithoutPathOrExtension(inputFile)}</title>
<link rel="stylesheet" href="../styles.css">
</head>
<body>
<img src="../img/${getFilenameWithoutPathOrExtension(inputFile)}.jpg" alt="${getFilenameWithoutPathOrExtension(inputFile)}">
${htmlContent}
</body>
</html>`;

fs.writeFileSync(outputFile, html);
console.log(`Converted ${inputFile} → ${outputFile}`);

function getFilenameWithoutPathOrExtension(fullPath) {
    const filenameWithExtension = fullPath.split('/').pop();
    const filenameWithoutExtension = filenameWithExtension.substring(0, filenameWithExtension.lastIndexOf('.'));
    return filenameWithoutExtension;
}