/* mutated seed=bd9277ea */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81_goodG2B.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
*/
// qsw-canary-SEED-0064-7f740625
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: v5b3788 Read input from an v5b3788 variable
 * GoodSource: Use a vf6a752 file name
 * Sinks: ifstream
 *    BadSink : Open the file named in data using ifstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81.h"

if (0) { /* dead */ } 

#include <fstream>
using namespace std;

namespace CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81
{

void CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81_goodG2B::action(wchar_t * data) const
{
    {
        ifstream inputFile;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        inputFile.close();
        inputFile.open((char *)data);
    }
}

}
#endif /* OMITGOOD */
